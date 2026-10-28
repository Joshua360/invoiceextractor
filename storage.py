import hashlib
import os
import re
import secrets

from datetime import (
    datetime,
    timedelta,
    timezone
)

from uuid import uuid4

import pandas as pd

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    delete,
    insert,
    select,
    update,
)

from sqlalchemy.exc import IntegrityError


# =====================================================
# OUTPUT COLUMNS
# =====================================================

OUTPUT_COLUMNS = [
    "DocNo",
    "DocType",
    "MovementDate",
    "PostedOn",
    "Delivered To",
    "InvoiceCode",
    "InvoiceDescription",
    "QtyCases",
    "PackText",
    "Customer",
]


# =====================================================
# SESSION TIME LIMIT
# =====================================================

SESSION_HOURS = int(
    os.getenv(
        "SESSION_HOURS",
        "24"
    )
)

if not 1 <= SESSION_HOURS <= 168:

    raise ValueError(
        "SESSION_HOURS must be "
        "between 1 and 168."
    )


# =====================================================
# DATABASE CONNECTION
# =====================================================

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    ""
).strip()


if not DATABASE_URL:

    if os.getenv("RENDER"):

        raise RuntimeError(
            "Set DATABASE_URL to a persistent "
            "PostgreSQL database on Render."
        )

    DATABASE_URL = (
        "sqlite:///invoice_app.db"
    )


if DATABASE_URL.startswith(
    "postgres://"
):

    DATABASE_URL = (
        "postgresql+psycopg://"
        + DATABASE_URL[len("postgres://"):]
    )

elif DATABASE_URL.startswith(
    "postgresql://"
):

    DATABASE_URL = (
        "postgresql+psycopg://"
        + DATABASE_URL[len("postgresql://"):]
    )


engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True
)

metadata = MetaData()


# =====================================================
# ANONYMOUS SESSIONS TABLE
# =====================================================

web_sessions = Table(

    "web_sessions",
    metadata,

    Column(
        "token_hash",
        String(64),
        primary_key=True
    ),

    Column(
        "created_at",
        DateTime(),
        nullable=False
    ),

    Column(
        "expires_at",
        DateTime(),
        nullable=False
    ),
)


# =====================================================
# BATCHES TABLE
# =====================================================

batches = Table(

    "batches",
    metadata,

    Column(
        "id",
        String(36),
        primary_key=True
    ),

    Column(
        "owner_id",
        String(255),
        nullable=False,
        index=True
    ),

    Column(
        "status",
        String(12),
        nullable=False,
        default="active"
    ),

    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False
    ),

    Column(
        "ended_at",
        DateTime(timezone=True)
    ),
)


# =====================================================
# INVOICES TABLE
# =====================================================

invoices = Table(

    "invoices",
    metadata,

    Column(
        "id",
        Integer,
        primary_key=True,
        autoincrement=True
    ),

    Column(
        "batch_id",
        String(36),
        ForeignKey("batches.id"),
        nullable=False
    ),

    Column(
        "doc_no",
        String(80),
        nullable=False
    ),

    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False
    ),

    UniqueConstraint(
        "batch_id",
        "doc_no",
        name="uq_batch_docno"
    ),
)


# =====================================================
# INVOICE LINES TABLE
# =====================================================

lines = Table(

    "invoice_lines",
    metadata,

    Column(
        "id",
        Integer,
        primary_key=True,
        autoincrement=True
    ),

    Column(
        "invoice_id",
        Integer,
        ForeignKey("invoices.id"),
        nullable=False
    ),

    Column(
        "line_no",
        Integer,
        nullable=False
    ),

    Column(
        "row_data",
        JSON,
        nullable=False
    ),

    UniqueConstraint(
        "invoice_id",
        "line_no",
        name="uq_invoice_line"
    ),
)


# Create database tables if missing.

metadata.create_all(
    engine
)


# =====================================================
# DATE HELPERS
# =====================================================

def utc_now():

    return datetime.now(
        timezone.utc
    )


def utc_naive():

    return datetime.now(
        timezone.utc
    ).replace(
        tzinfo=None
    )


# =====================================================
# SESSION TOKEN HELPERS
# =====================================================

def token_digest(token):

    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def owner_from_digest(digest):

    return "guest:" + digest


# =====================================================
# DELETE A SESSION AND ITS DATA
# =====================================================

def delete_session_record(
    connection,
    digest
):

    owner_id = owner_from_digest(
        digest
    )

    batch_ids = (
        select(batches.c.id)
        .where(
            batches.c.owner_id == owner_id
        )
    )

    invoice_ids = (
        select(invoices.c.id)
        .where(
            invoices.c.batch_id.in_(
                batch_ids
            )
        )
    )

    # Delete product rows first.

    connection.execute(

        delete(lines).where(
            lines.c.invoice_id.in_(
                invoice_ids
            )
        )
    )

    # Then delete invoices.

    connection.execute(

        delete(invoices).where(
            invoices.c.batch_id.in_(
                batch_ids
            )
        )
    )

    # Delete associated batches.

    connection.execute(

        delete(batches).where(
            batches.c.owner_id == owner_id
        )
    )

    # Finally delete the session.

    connection.execute(

        delete(web_sessions).where(
            web_sessions.c.token_hash == digest
        )
    )


# =====================================================
# DELETE EXPIRED SESSIONS
# =====================================================

def purge_expired_sessions(
    connection
):

    expired_tokens = connection.execute(

        select(
            web_sessions.c.token_hash
        )

        .where(
            web_sessions.c.expires_at
            <= utc_naive()
        )

    ).scalars().all()

    for digest in expired_tokens:

        delete_session_record(
            connection,
            digest
        )


# =====================================================
# CREATE OR RECOVER SESSION
# =====================================================

def open_session(
    provided_token=None
):

    with engine.begin() as connection:

        purge_expired_sessions(
            connection
        )

        # Check whether the browser's
        # existing session is valid.

        if (
            provided_token
            and re.fullmatch(
                r"[A-Za-z0-9_-]{40,100}",
                provided_token
            )
        ):

            digest = token_digest(
                provided_token
            )

            expires_at = connection.execute(

                select(
                    web_sessions.c.expires_at
                )

                .where(
                    web_sessions.c.token_hash
                    == digest,

                    web_sessions.c.expires_at
                    > utc_naive()
                )

            ).scalar_one_or_none()

            if expires_at is not None:

                return (
                    provided_token,
                    owner_from_digest(digest),
                    expires_at,
                    False
                )

        # No valid session was found.
        # Create a new random token.

        token = secrets.token_urlsafe(
            32
        )

        digest = token_digest(
            token
        )

        created_at = utc_naive()

        expires_at = (
            created_at
            + timedelta(
                hours=SESSION_HOURS
            )
        )

        connection.execute(

            insert(web_sessions).values(

                token_hash=digest,

                created_at=created_at,

                expires_at=expires_at
            )
        )

        return (
            token,
            owner_from_digest(digest),
            expires_at,
            True
        )


# =====================================================
# END SESSION MANUALLY
# =====================================================

def end_session(token):

    if not token:
        return

    if not re.fullmatch(
        r"[A-Za-z0-9_-]{40,100}",
        token
    ):
        return

    digest = token_digest(
        token
    )

    with engine.begin() as connection:

        delete_session_record(
            connection,
            digest
        )


# =====================================================
# VERIFY SESSION IS STILL ACTIVE
# =====================================================

def assert_session_alive(
    connection,
    owner_id
):

    if not owner_id.startswith(
        "guest:"
    ):

        raise PermissionError(
            "Invalid session."
        )

    digest = owner_id[
        len("guest:"):
    ]

    existing = connection.execute(

        select(
            web_sessions.c.token_hash
        )

        .where(
            web_sessions.c.token_hash
            == digest,

            web_sessions.c.expires_at
            > utc_naive()
        )

    ).scalar_one_or_none()

    if existing is None:

        raise PermissionError(
            "Session expired. Refresh the app "
            "to create a new session."
        )


# =====================================================
# CREATE OR RECOVER ACTIVE BATCH
# =====================================================

def get_or_create_active_batch(
    owner_id
):

    with engine.begin() as connection:

        assert_session_alive(
            connection,
            owner_id
        )

        existing_batch_id = (
            connection.execute(

                select(batches.c.id)

                .where(
                    batches.c.owner_id == owner_id,

                    batches.c.status == "active"
                )

                .order_by(
                    batches.c.created_at.desc()
                )

                .limit(1)

            ).scalar_one_or_none()
        )

        if existing_batch_id:

            return existing_batch_id

        batch_id = str(
            uuid4()
        )

        connection.execute(

            insert(batches).values(

                id=batch_id,

                owner_id=owner_id,

                status="active",

                created_at=utc_now()
            )
        )

        return batch_id


# =====================================================
# LIST SAVED BATCHES
# =====================================================

def list_user_batches(owner_id):

    with engine.connect() as connection:

        assert_session_alive(
            connection,
            owner_id
        )

        result = connection.execute(

            select(
                batches.c.id,
                batches.c.status,
                batches.c.created_at
            )

            .where(
                batches.c.owner_id == owner_id
            )

            .order_by(
                batches.c.created_at.desc()
            )
        )

        return result.mappings().all()


# =====================================================
# VERIFY BATCH ACCESS
# =====================================================

def check_batch_access(
    connection,
    owner_id,
    batch_id,
    active_required=False
):

    assert_session_alive(
        connection,
        owner_id
    )

    status = connection.execute(

        select(
            batches.c.status
        )

        .where(
            batches.c.id == batch_id,

            batches.c.owner_id == owner_id
        )

    ).scalar_one_or_none()

    if status is None:

        raise PermissionError(
            "You cannot access this batch."
        )

    if (
        active_required
        and status != "active"
    ):

        raise ValueError(
            "This batch is closed."
        )


# =====================================================
# DETECT DUPLICATE INVOICE
# =====================================================

def batch_has_invoice(
    owner_id,
    batch_id,
    doc_no
):

    doc_no = (
        doc_no.strip().upper()
    )

    with engine.connect() as connection:

        check_batch_access(
            connection,
            owner_id,
            batch_id
        )

        existing = connection.execute(

            select(
                invoices.c.id
            )

            .where(
                invoices.c.batch_id == batch_id,

                invoices.c.doc_no == doc_no
            )

            .limit(1)

        ).scalar_one_or_none()

        return existing is not None


# =====================================================
# SAVE INVOICE AND PRODUCT ROWS
# =====================================================

def add_invoice(
    owner_id,
    batch_id,
    doc_no,
    row_dicts
):

    doc_no = (
        doc_no.strip().upper()
    )

    if not doc_no or not row_dicts:

        raise ValueError(
            "Invoice number and product "
            "rows are required."
        )

    try:

        with engine.begin() as connection:

            check_batch_access(
                connection,
                owner_id,
                batch_id,
                active_required=True
            )

            invoice_id = connection.execute(

                insert(invoices).values(

                    batch_id=batch_id,

                    doc_no=doc_no,

                    created_at=utc_now()

                ).returning(
                    invoices.c.id
                )

            ).scalar_one()

            product_rows = []

            for index, row in enumerate(
                row_dicts,
                start=1
            ):

                product_rows.append({

                    "invoice_id": invoice_id,

                    "line_no": index,

                    "row_data": {

                        column: str(
                            row.get(
                                column,
                                ""
                            )
                        )

                        for column
                        in OUTPUT_COLUMNS
                    }
                })

            connection.execute(

                insert(lines),

                product_rows
            )

    except IntegrityError as error:

        raise ValueError(
            f"Duplicate blocked: {doc_no} "
            f"is already in this batch."
        ) from error


# =====================================================
# LOAD SAVED BATCH DATA
# =====================================================

def get_batch_dataframe(
    owner_id,
    batch_id
):

    with engine.connect() as connection:

        check_batch_access(
            connection,
            owner_id,
            batch_id
        )

        rows = connection.execute(

            select(
                lines.c.row_data
            )

            .join(
                invoices,
                lines.c.invoice_id
                == invoices.c.id
            )

            .where(
                invoices.c.batch_id == batch_id
            )

            .order_by(
                invoices.c.id,
                lines.c.line_no
            )

        ).scalars().all()

    return pd.DataFrame(
        rows,
        columns=OUTPUT_COLUMNS
    )


# =====================================================
# END ONE BATCH
# =====================================================

def end_batch(
    owner_id,
    batch_id
):

    with engine.begin() as connection:

        check_batch_access(
            connection,
            owner_id,
            batch_id,
            active_required=True
        )

        connection.execute(

            update(batches)

            .where(
                batches.c.id == batch_id
            )

            .values(
                status="ended",
                ended_at=utc_now()
            )
        )


        