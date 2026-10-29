import re
import pdfplumber


# =====================================================
# REGULAR EXPRESSION PATTERNS
# =====================================================

DATE_PATTERN = r"(\d{1,2}/\d{1,2}/\d{4})"

PACK_PATTERN = re.compile(
    r"\b(\d+)\s*[xX×]\s*"
    r"(\d+(?:\.\d+)?)\s*"
    r"(ml|l|ltr|litre|cl)\b",
    re.IGNORECASE
)

SIZE_PATTERN = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*"
    r"(ml|l|ltr|litre|cl)\b",
    re.IGNORECASE
)

NUMBER_PATTERN = re.compile(
    r"\d+(?:\.\d+)?"
)

YEAR_PATTERN = re.compile(
    r"(?:19|20)\d{2}"
)


# =====================================================
# TEXT UTILITIES
# =====================================================

def clean_text(value):
    return re.sub(
        r"\s+",
        " ",
        str(value or "")
    ).strip()


def normalize(value):
    return clean_text(value).casefold()


def word_center(word):
    return (
        float(word["x0"]) +
        float(word["x1"])
    ) / 2


# =====================================================
# GROUP PDF WORDS INTO VISUAL LINES
# =====================================================

def group_words_into_lines(
    words,
    y_tolerance=3
):
    groups = []

    sorted_words = sorted(
        words,
        key=lambda w: (
            float(w["top"]),
            float(w["x0"])
        )
    )

    for word in sorted_words:

        group = next(
            (
                g for g in groups[-2:]
                if abs(
                    float(word["top"]) - g["top"]
                ) <= y_tolerance
            ),
            None
        )

        if group is None:

            group = {
                "top": float(word["top"]),
                "words": []
            }

            groups.append(group)

        group["words"].append(word)

    for group in groups:

        group["words"].sort(
            key=lambda w: float(w["x0"])
        )

        group["text"] = clean_text(
            " ".join(
                w["text"]
                for w in group["words"]
            )
        )

    return groups


# =====================================================
# EXTRACT PDF CONTENT
# =====================================================

def extract_pages(uploaded_file):

    uploaded_file.seek(0)

    pages_data = []

    with pdfplumber.open(uploaded_file) as pdf:

        for page_number, page in enumerate(
            pdf.pages,
            start=1
        ):

            words = page.extract_words(
                x_tolerance=2,
                y_tolerance=3,
                keep_blank_chars=False,
                use_text_flow=False
            ) or []

            pages_data.append({

                "page_number": page_number,

                "width": float(page.width),

                "lines": group_words_into_lines(
                    words
                )
            })

    return pages_data


# =====================================================
# INVOICE HEADER EXTRACTION
# =====================================================

def find_date(label, text):

    for line in text.splitlines():

        match = re.search(
            rf"{label}\s*:?\s*{DATE_PATTERN}",
            line,
            re.IGNORECASE
        )

        if match:
            return match.group(1)

    return ""


def find_invoice_number(text):

    match = re.search(
        r"\bPSI\d+\b",
        text,
        re.IGNORECASE
    )

    if match:
        return match.group(0).upper()

    return ""


def find_customer(lines):

    for line in lines:

        text = line["text"]

        if re.match(
            r"^Customer\b",
            text,
            re.IGNORECASE
        ):

            return re.sub(
                r"^Customer\s*:?\s*",
                "",
                text,
                flags=re.IGNORECASE
            ).strip()

    return ""


def find_delivery_address(lines):

    stop_labels = (
        "Contact Name",
        "Contact No.",
        "Phone No.",
        "VAT Reg No.",
        "Liquor License No.",
        "Company Reg No.",
        "Your Reference",
        "Invoice No.",
        "SO No."
    )

    for index, line in enumerate(lines):

        text = line["text"]

        if not re.match(
            r"^Delivery\s+Address\b",
            text,
            re.IGNORECASE
        ):
            continue

        first_value = re.sub(
            r"^Delivery\s+Address\s*:?\s*",
            "",
            text,
            flags=re.IGNORECASE
        ).strip()

        parts = []

        if first_value:
            parts.append(first_value)

        for next_line in lines[index + 1:]:

            next_text = next_line["text"].strip()

            if not next_text:
                break

            if any(
                next_text.casefold().startswith(
                    label.casefold()
                )
                for label in stop_labels
            ):
                break

            parts.append(next_text)

        return ", ".join(parts)

    return ""


# =====================================================
# IDENTIFY PRODUCT TABLE
# =====================================================

def is_item_header(text):

    text = normalize(text)

    required_labels = (
        "code",
        "description",
        "quantity",
        "unit of measure"
    )

    return all(
        label in text
        for label in required_labels
    )


def is_totals_line(text):

    text = normalize(text)

    stop_labels = (
        "total ksh excl",
        "total excl. vat",
        "total ksh incl",
        "grand total"
    )

    return any(
        label in text
        for label in stop_labels
    )


# =====================================================
# NEW: IDENTIFY INVOICE FOOTER
# =====================================================

def is_footer_line(text):
    """
    Detect content that belongs to the invoice
    footer rather than the product table.

    Especially useful for sample invoices that
    do not contain a totals section.
    """

    text = clean_text(text)

    footer_patterns = [

        # Normal invoice footer
        r"^thanks\s+for\s+your\s+business\b",

        # Banking details footer
        r"^banking\s+details\b",

        # Sample invoice footer
        r"^email\s+us\b",

        # Other email labels
        r"^email\s+address\s*:?",

        r"^e-?mail\s*:",

        # Supplier email addresses
        r"^mwkorders@\S+",

        # Standalone email address
        r"^[^\s@]+@[^\s@]+\.[^\s@]+$",

        # Common footer labels
        r"^printed\s+by\b",

        r"^generated\s+by\b",
    ]

    return any(
        re.search(
            pattern,
            text,
            re.IGNORECASE
        )
        for pattern in footer_patterns
    )


# =====================================================
# FIND COLUMN POSITIONS
# =====================================================

def find_header_span(words, tokens):

    terms = [
        normalize(word["text"])
        for word in words
    ]

    for start in range(
        len(terms) - len(tokens) + 1
    ):

        if (
            terms[start:start + len(tokens)]
            == list(tokens)
        ):

            matching_words = words[
                start:start + len(tokens)
            ]

            return (
                float(matching_words[0]["x0"]) +
                float(matching_words[-1]["x1"])
            ) / 2

    return None


def get_header_boundaries(
    words,
    page_width
):

    words = sorted(
        words,
        key=lambda w: w["x0"]
    )

    centers = [

        find_header_span(
            words,
            ("code",)
        ),

        find_header_span(
            words,
            ("description",)
        ),

        find_header_span(
            words,
            ("quantity",)
        ),

        find_header_span(
            words,
            ("unit", "of", "measure")
        ),

        find_header_span(
            words,
            ("unit", "price")
        )
    ]

    if any(
        value is None
        for value in centers[:4]
    ):
        return None

    if centers[4] is None:
        centers[4] = page_width * 0.78

    return [
        (
            centers[i] +
            centers[i + 1]
        ) / 2
        for i in range(4)
    ]


# =====================================================
# SPLIT WORDS INTO PRODUCT COLUMNS
# =====================================================

def split_line_into_columns(
    words,
    boundaries
):

    parts = [
        [],
        [],
        [],
        []
    ]

    for word in sorted(
        words,
        key=lambda w: w["x0"]
    ):

        x = word_center(word)

        column_index = next(
            (
                i
                for i, boundary in enumerate(
                    boundaries
                )
                if x < boundary
            ),
            4
        )

        if column_index < 4:

            parts[column_index].append(
                word["text"]
            )

    column_names = (
        "InvoiceCode",
        "InvoiceDescription",
        "QtyCases",
        "PackText"
    )

    return {
        key: clean_text(
            " ".join(value)
        )
        for key, value in zip(
            column_names,
            parts
        )
    }


# =====================================================
# PACK TEXT REPAIR
# =====================================================

def format_pack_text(match):

    count, size, unit = match.groups()

    if unit.lower() in (
        "l",
        "ltr",
        "litre"
    ):
        unit = "L"
    else:
        unit = unit.lower()

    return f"{count} x {size}{unit}"


def is_complete_pack_text(text):

    return bool(
        PACK_PATTERN.fullmatch(
            clean_text(text)
        )
    )


# =====================================================
# CLEAN PRODUCT FIELDS
# =====================================================

def refine_first_line(columns, original_text):

    description = clean_text(
        columns["InvoiceDescription"]
    )

    quantity = clean_text(
        columns["QtyCases"]
    )

    pack_text = clean_text(
        columns["PackText"]
    )

    warnings = []

    # ------------------------------------------
    # 1. Repair pack text merged into quantity
    # ------------------------------------------

    pack_match = PACK_PATTERN.search(
        quantity
    )

    if pack_match:

        if not is_complete_pack_text(
            pack_text
        ):

            pack_text = format_pack_text(
                pack_match
            )

        quantity = clean_text(
            quantity[:pack_match.start()]
            + " "
            + quantity[pack_match.end():]
        )

    # ------------------------------------------
    # 2. Repair incomplete PackText
    # ------------------------------------------

    if not is_complete_pack_text(
        pack_text
    ):

        full_pack = PACK_PATTERN.search(
            original_text
        )

        if full_pack:

            pack_text = format_pack_text(
                full_pack
            )

        elif re.fullmatch(
            r"\d+",
            pack_text
        ):

            size_match = SIZE_PATTERN.search(
                original_text
            )

            if size_match:

                size = size_match.group(1)
                unit = size_match.group(2)

                if unit.lower() in (
                    "l",
                    "ltr",
                    "litre"
                ):
                    unit = "L"
                else:
                    unit = unit.lower()

                pack_text = (
                    f"{pack_text} x {size}{unit}"
                )

    # ------------------------------------------
    # 3. Identify numeric quantity tokens
    # ------------------------------------------

    tokens = quantity.split()

    numeric_tokens = [
        token
        for token in tokens
        if NUMBER_PATTERN.fullmatch(
            token.replace(",", "")
        )
    ]

    # ------------------------------------------
    # 4. Identify misplaced vintage years
    # ------------------------------------------

    vintage_years = []

    if len(numeric_tokens) > 1:

        vintage_years = [
            token
            for token in numeric_tokens
            if YEAR_PATTERN.fullmatch(token)
        ]

    actual_quantities = [
        token
        for token in numeric_tokens
        if token not in vintage_years
    ]

    # ------------------------------------------
    # 5. Move misplaced description words
    # ------------------------------------------

    misplaced_words = [
        token
        for token in tokens
        if token not in numeric_tokens
    ]

    if misplaced_words:

        description = clean_text(
            description
            + " "
            + " ".join(misplaced_words)
        )

    # ------------------------------------------
    # 6. Enforce numeric-only QtyCases
    # ------------------------------------------

    if (
        len(actual_quantities) == 1
        and YEAR_PATTERN.fullmatch(
            actual_quantities[0]
        )
        and not vintage_years
    ):

        description = clean_text(
            description
            + " "
            + actual_quantities[0]
        )

        quantity = ""

        warnings.append(
            "Only a possible vintage year "
            "was found in QtyCases."
        )

    elif len(actual_quantities) == 1:

        quantity = actual_quantities[
            0
        ].replace(",", "")

    else:

        quantity = ""

        warnings.append(
            "QtyCases could not be "
            "identified unambiguously."
        )

    # ------------------------------------------
    # 7. Move vintage years to description
    # ------------------------------------------

    for year in vintage_years:

        if not description.endswith(
            " " + year
        ):

            description = clean_text(
                description + " " + year
            )

    return {

        "InvoiceCode": clean_text(
            columns["InvoiceCode"]
        ),

        "InvoiceDescription": description,

        "QtyCases": quantity,

        "PackText": pack_text,

        "_warnings": warnings
    }


# =====================================================
# DECIDE IF LINE STARTS A NEW PRODUCT
# =====================================================

def is_new_item(columns):

    code = columns["InvoiceCode"]

    quantity = columns["QtyCases"]

    pack = columns["PackText"]

    if (
        YEAR_PATTERN.fullmatch(code)
        and not quantity
        and not pack
    ):
        return False

    if code and (quantity or pack):
        return True

    if (
        code
        and not YEAR_PATTERN.fullmatch(code)
        and columns["InvoiceDescription"]
    ):
        return True

    return False


# =====================================================
# EXTRACT WRAPPED DESCRIPTION TEXT
# =====================================================

def get_continuation_text(
    words,
    boundaries
):

    text_parts = []

    for word in sorted(
        words,
        key=lambda w: w["x0"]
    ):

        x = word_center(word)

        if (
            boundaries[0]
            <= x
            < boundaries[3]
        ):

            text_parts.append(
                str(word["text"])
            )

    return clean_text(
        " ".join(text_parts)
    )


# =====================================================
# KEEP VINTAGE YEAR AT END OF DESCRIPTION
# =====================================================

def keep_vintage_at_end(description):

    words = clean_text(
        description
    ).split()

    vintage_words = [
        word
        for word in words
        if YEAR_PATTERN.fullmatch(word)
    ]

    other_words = [
        word
        for word in words
        if not YEAR_PATTERN.fullmatch(word)
    ]

    return clean_text(
        " ".join(
            other_words + vintage_words
        )
    )


# =====================================================
# NEW: DETECT LARGE GAPS BELOW PRODUCT ROWS
# =====================================================

def is_large_gap(
    previous_top,
    current_top,
    threshold=65
):
    """
    A large vertical gap may indicate
    the product table has ended.

    The check is only used for lines that
    do not look like new product rows.
    """

    if previous_top is None:
        return False

    return (
        float(current_top)
        - float(previous_top)
    ) > threshold


# =====================================================
# PARSE ALL PRODUCT ROWS
# =====================================================

def parse_line_items(pages_data):

    items = []

    current_item = None

    finished = False

    def save_current_item():

        nonlocal current_item

        if current_item:

            # Preserve the description words
            # while keeping the vintage at the end.

            current_item[
                "InvoiceDescription"
            ] = keep_vintage_at_end(
                current_item[
                    "InvoiceDescription"
                ]
            )

            if (
                current_item["InvoiceCode"]
                and current_item[
                    "InvoiceDescription"
                ]
            ):

                items.append(
                    current_item
                )

            current_item = None

    for page in pages_data:

        inside_table = False

        boundaries = None

        # Track the last product/continuation
        # line within this page only.

        last_product_top = None

        for line in page["lines"]:

            text = line["text"]

            # -----------------------------------
            # Find the product table header
            # -----------------------------------

            if is_item_header(text):

                inside_table = True

                boundaries = (
                    get_header_boundaries(
                        line["words"],
                        page["width"]
                    )
                )

                last_product_top = None

                continue

            if (
                not inside_table
                or boundaries is None
            ):
                continue

            # -----------------------------------
            # STOP RULE 1:
            # Normal invoice totals
            # -----------------------------------

            if is_totals_line(text):

                save_current_item()

                finished = True

                break

            # -----------------------------------
            # STOP RULE 2:
            # Invoice footer content
            #
            # This fixes the sample invoice
            # without a totals section.
            # -----------------------------------

            if is_footer_line(text):

                save_current_item()

                finished = True

                break

            # -----------------------------------
            # Split the current visual line
            # -----------------------------------

            columns = split_line_into_columns(
                line["words"],
                boundaries
            )

            if not any(
                columns.values()
            ):
                continue

            new_product = is_new_item(
                columns
            )

            # -----------------------------------
            # STOP RULE 3:
            # Large gap followed by text that
            # does not look like another product.
            #
            # This helps with unfamiliar footers
            # that don't contain known labels.
            # -----------------------------------

            if (
                current_item is not None
                and not new_product
                and is_large_gap(
                    last_product_top,
                    line["top"]
                )
            ):

                save_current_item()

                finished = True

                break

            # -----------------------------------
            # Normal new product row
            # -----------------------------------

            if new_product:

                save_current_item()

                current_item = refine_first_line(
                    columns,
                    text
                )

                last_product_top = line["top"]

            # -----------------------------------
            # Description continuation
            # -----------------------------------

            elif current_item:

                continuation = (
                    get_continuation_text(
                        line["words"],
                        boundaries
                    )
                )

                if continuation:

                    current_item[
                        "InvoiceDescription"
                    ] = clean_text(
                        current_item[
                            "InvoiceDescription"
                        ]
                        + " "
                        + continuation
                    )

                    last_product_top = line["top"]

        if finished:
            break

    # Save the last product when there are
    # no totals or recognizable footer.

    save_current_item()

    return items


# =====================================================
# MAIN INVOICE PARSER
# =====================================================

def parse_invoice(uploaded_file):

    pages_data = extract_pages(
        uploaded_file
    )

    raw_text = "\n".join(
        line["text"]
        for page in pages_data
        for line in page["lines"]
    )

    first_page_lines = (
        pages_data[0]["lines"]
        if pages_data
        else []
    )

    doc_no = find_invoice_number(
        raw_text
    )

    posted_on = find_date(
        r"Posting\s+Date",
        raw_text
    )

    promised_date = find_date(
        r"Promised\s+Delivery\s+Date",
        raw_text
    )

    # MovementDate priority:
    # Promised Delivery Date first,
    # otherwise Posting Date.

    movement_date = (
        promised_date or posted_on
    )

    product_rows = parse_line_items(
        pages_data
    )

    warnings = []

    for index, item in enumerate(
        product_rows,
        start=1
    ):

        for warning in item.pop(
            "_warnings",
            []
        ):

            warnings.append(
                f"Line {index}: {warning}"
            )

        if not NUMBER_PATTERN.fullmatch(
            item["QtyCases"]
        ):

            warnings.append(
                f"Line {index}: "
                f"QtyCases is missing or invalid."
            )

    return {

        "DocNo": doc_no,

        "DocType": "Invoice",

        "MovementDate": movement_date,

        "PostedOn": posted_on,

        "Delivered To":
            find_delivery_address(
                first_page_lines
            ),

        "Customer":
            find_customer(
                first_page_lines
            ),

        "rows": product_rows,

        "warnings": warnings,

        "raw_text": raw_text,

        "line_preview": [
            (
                f"-- PAGE {page['page_number']} --\n"
                + "\n".join(
                    line["text"]
                    for line in page["lines"][:50]
                )
            )
            for page in pages_data
        ],
    }