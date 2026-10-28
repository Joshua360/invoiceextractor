import io
import re
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from parser_invoice import parse_invoice

from storage import (
    OUTPUT_COLUMNS,
    SESSION_HOURS,
    add_invoice,
    batch_has_invoice,
    end_batch,
    end_session,
    get_batch_dataframe,
    get_or_create_active_batch,
    list_user_batches,
    open_session,
)


st.set_page_config(
    page_title="Invoice Data Extractor",
    layout="wide"
)

st.title("Invoice Data Extractor")

st.caption(
    "Upload invoices one at a time. "
    "Progress is saved across browser refreshes."
)


# =====================================================
# HELPER FUNCTIONS
# =====================================================

def valid_date(text):

    if not re.fullmatch(
        r"\d{2}/\d{2}/\d{4}",
        text
    ):
        return False

    try:
        datetime.strptime(
            text,
            "%d/%m/%Y"
        )
        return True

    except ValueError:
        return False


def excel_bytes(df):

    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl"
    ) as writer:

        df.to_excel(
            writer,
            index=False,
            sheet_name="Invoices"
        )

        sheet = writer.sheets["Invoices"]

        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions

        for column in sheet.columns:

            letter = column[0].column_letter

            width = max(
                len(str(cell.value or ""))
                for cell in column
            ) + 2

            sheet.column_dimensions[
                letter
            ].width = min(
                max(width, 12),
                65
            )

    return output.getvalue()


# =====================================================
# AUTOMATIC SESSION CREATION / RECOVERY
# =====================================================

try:

    saved_token = st.query_params.get(
        "sid"
    )

    (
        session_token,
        owner_id,
        expires_at,
        created_new
    ) = open_session(saved_token)

    if created_new:

        # Store the session ID in the browser URL.
        # It will remain there when the page refreshes.

        st.query_params["sid"] = session_token

        st.session_state.pop(
            "selected_batch",
            None
        )

        st.session_state.pop(
            "last_preview",
            None
        )

        if saved_token:

            st.warning(
                "Your previous session has expired "
                "or ended. A new session was created."
            )

    active_id = get_or_create_active_batch(
        owner_id
    )

    batches = list_user_batches(
        owner_id
    )

except Exception as error:

    st.error(
        f"Could not initialize session: {error}"
    )

    st.stop()


# =====================================================
# SESSION INFORMATION
# =====================================================

eat = timezone(
    timedelta(hours=3)
)

expiry_local = (
    expires_at
    .replace(tzinfo=timezone.utc)
    .astimezone(eat)
)

st.info(
    f"Session duration: {SESSION_HOURS} hours. "
    f"Expires: {expiry_local:%d %b %Y at %H:%M} EAT. "
    "Your progress is automatically saved. "
    "Keep the complete URL to return to this session."
)


# =====================================================
# SIDEBAR
# =====================================================

with st.sidebar:

    st.subheader("Temporary Session")

    st.caption(
        "No login required. "
        "Your complete browser URL is your "
        "private session access link."
    )

    remaining = max(
        timedelta(0),
        expires_at
        - datetime.now(
            timezone.utc
        ).replace(tzinfo=None)
    )

    remaining_hours = (
        remaining.days * 24
        + remaining.seconds // 3600
    )

    remaining_minutes = (
        remaining.seconds % 3600
    ) // 60

    st.write(
        f"Time remaining: "
        f"**{remaining_hours}h "
        f"{remaining_minutes}m**"
    )

    st.divider()

    st.subheader("Saved Batches")

    batch_labels = {

        batch["id"]: (
            f"{'Current' if batch['status'] == 'active' else 'Closed'} "
            f"- {batch['created_at']:%d %b %Y %H:%M} "
            f"- {batch['id'][:8]}"
        )

        for batch in batches
    }

    selected_id = st.selectbox(
        "Select a batch",
        options=[
            batch["id"]
            for batch in batches
        ],
        format_func=lambda batch_id: (
            batch_labels[batch_id]
        ),
        key="selected_batch"
    )

    st.divider()

    confirm_end = st.checkbox(
        "I have downloaded my data and "
        "want to delete this session"
    )

    if st.button(
        "End Session and Delete All Data",
        disabled=not confirm_end,
        use_container_width=True
    ):

        end_session(
            session_token
        )

        st.query_params.clear()

        st.session_state.clear()

        st.rerun()


# =====================================================
# LOAD CURRENT BATCH
# =====================================================

is_active = (
    selected_id == active_id
)

try:

    df = get_batch_dataframe(
        owner_id,
        selected_id
    )

except Exception as error:

    st.error(
        f"Could not load saved batch: {error}"
    )

    st.stop()


if not is_active:

    st.info(
        "This batch is closed. "
        "You can view or download it, "
        "but cannot add invoices."
    )


# =====================================================
# INVOICE UPLOAD
# =====================================================

with st.form(
    "upload_invoice_form"
):

    invoice_file = st.file_uploader(
        "Upload Invoice PDF",
        type=["pdf"],
        disabled=not is_active
    )

    col1, col2 = st.columns(2)

    with col1:

        manual_movement_date = st.text_input(
            "Manual Movement Date (optional)",
            placeholder="DD/MM/YYYY",
            disabled=not is_active
        )

    with col2:

        manual_delivered_to = st.text_input(
            "Manual Delivered To (optional)",
            placeholder="Enter delivery destination",
            disabled=not is_active
        )

    process_clicked = st.form_submit_button(
        "Process Documents",
        disabled=not is_active,
        use_container_width=True
    )


# =====================================================
# PROCESS INVOICE
# =====================================================

if process_clicked:

    entered_date = (
        manual_movement_date.strip()
    )

    entered_destination = (
        manual_delivered_to.strip()
    )

    if invoice_file is None:

        st.error(
            "Please upload an invoice PDF first."
        )

    elif (
        entered_date
        and not valid_date(entered_date)
    ):

        st.error(
            "Manual Movement Date must be "
            "a valid date in DD/MM/YYYY format."
        )

    else:

        try:

            invoice_data = parse_invoice(
                invoice_file
            )

            st.session_state[
                "last_preview"
            ] = invoice_data

            doc_no = str(
                invoice_data.get(
                    "DocNo",
                    ""
                )
            ).strip().upper()

            if not doc_no:

                st.error(
                    "No invoice number found. "
                    "Nothing was saved."
                )

            elif batch_has_invoice(
                owner_id,
                selected_id,
                doc_no
            ):

                st.warning(
                    f"Duplicate blocked: {doc_no} "
                    f"already exists in this batch."
                )

            elif not invoice_data.get("rows"):

                st.error(
                    "No product rows were extracted. "
                    "Nothing was saved."
                )

            elif invoice_data.get("warnings"):

                st.error(
                    "Some quantities could not be "
                    "read reliably. Nothing was saved."
                )

                for warning in invoice_data["warnings"]:

                    st.warning(warning)

            else:

                # MovementDate priority:
                # 1. Manual entry
                # 2. Promised Delivery Date
                # 3. Posting Date
                #
                # The invoice parser handles
                # the automatic date fallback.

                final_movement_date = (
                    entered_date
                    or invoice_data.get(
                        "MovementDate",
                        ""
                    )
                )

                final_delivered_to = (
                    entered_destination
                    or invoice_data.get(
                        "Delivered To",
                        ""
                    )
                )

                new_rows = []

                for item in invoice_data["rows"]:

                    invoice_row = {

                        "DocNo": doc_no,

                        "DocType": "Invoice",

                        "MovementDate":
                            final_movement_date,

                        "PostedOn":
                            invoice_data.get(
                                "PostedOn",
                                ""
                            ),

                        "Delivered To":
                            final_delivered_to,

                        "InvoiceCode":
                            item["InvoiceCode"],

                        "InvoiceDescription":
                            item["InvoiceDescription"],

                        "QtyCases":
                            item["QtyCases"],

                        "PackText":
                            item["PackText"],

                        "Customer":
                            invoice_data.get(
                                "Customer",
                                ""
                            ),
                    }

                    new_rows.append(
                        invoice_row
                    )

                # Immediately save to the database.
                # This is what preserves progress
                # when the browser refreshes.

                add_invoice(
                    owner_id,
                    selected_id,
                    doc_no,
                    new_rows
                )

                st.success(
                    f"Invoice {doc_no} saved successfully. "
                    f"{len(new_rows)} product rows added."
                )

                # Reload saved data

                df = get_batch_dataframe(
                    owner_id,
                    selected_id
                )

        except ValueError as error:

            st.warning(
                str(error)
            )

        except Exception as error:

            st.error(
                f"Invoice processing failed: {error}"
            )


# =====================================================
# LATEST EXTRACTION PREVIEW
# =====================================================

if st.session_state.get(
    "last_preview"
):

    preview = st.session_state[
        "last_preview"
    ]

    with st.expander(
        "Latest Extraction Preview / Debugging"
    ):

        st.write({

            key: preview.get(key, "")

            for key in (
                "DocNo",
                "MovementDate",
                "PostedOn",
                "Delivered To",
                "Customer"
            )
        })

        st.dataframe(
            pd.DataFrame(
                preview.get("rows", [])
            ),
            use_container_width=True
        )

        st.text_area(
            "Extracted PDF Text",
            preview.get("raw_text", ""),
            height=200
        )


# =====================================================
# CURRENT BATCH TABLE
# =====================================================

st.subheader(
    "Current Batch"
    if is_active
    else "Saved Batch"
)

st.dataframe(
    df[OUTPUT_COLUMNS],
    use_container_width=True,
    hide_index=True
)


# =====================================================
# BATCH SUMMARY
# =====================================================

total_invoices = (
    df["DocNo"].nunique()
    if not df.empty
    else 0
)

st.write(
    f"**Invoices:** {total_invoices} "
    f"| **Product Rows:** {len(df)}"
)


# =====================================================
# DOWNLOAD EXCEL
# =====================================================

if not df.empty:

    st.download_button(
        "Download Excel",
        data=excel_bytes(df),
        file_name=(
            f"invoice_batch_"
            f"{selected_id[:8]}.xlsx"
        ),
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        use_container_width=True
    )


# =====================================================
# END CURRENT BATCH
# =====================================================

if is_active:

    st.divider()

    st.caption(
        "Close this batch to begin a new one. "
        "Closed batches remain available "
        "until your session expires."
    )

    if st.button(
        "End Batch and Start Another"
    ):

        end_batch(
            owner_id,
            selected_id
        )

        get_or_create_active_batch(
            owner_id
        )

        st.session_state.pop(
            "selected_batch",
            None
        )

        st.session_state.pop(
            "last_preview",
            None
        )

        st.rerun()