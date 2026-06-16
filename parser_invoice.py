import re
import pdfplumber


PACK_PATTERN = re.compile(
    r"\b(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*(ml|l|ltr|litre|cl)\b",
    re.IGNORECASE,
)

SIZE_ONLY_PATTERN = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(ml|l|ltr|litre|cl)\b",
    re.IGNORECASE,
)


def clean_text(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", " ").strip()


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", clean_text(value)).strip().lower()


def word_center(word) -> float:
    return (float(word["x0"]) + float(word["x1"])) / 2


def group_words_into_lines(words, y_tolerance=3):
    if not words:
        return []

    words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines = []
    current_line = {
        "top": words[0]["top"],
        "words": [words[0]],
    }

    for word in words[1:]:
        if abs(word["top"] - current_line["top"]) <= y_tolerance:
            current_line["words"].append(word)
        else:
            current_line["words"] = sorted(current_line["words"], key=lambda w: w["x0"])
            current_line["text"] = " ".join(clean_text(w["text"]) for w in current_line["words"])
            lines.append(current_line)

            current_line = {
                "top": word["top"],
                "words": [word],
            }

    current_line["words"] = sorted(current_line["words"], key=lambda w: w["x0"])
    current_line["text"] = " ".join(clean_text(w["text"]) for w in current_line["words"])
    lines.append(current_line)

    return lines


def extract_pages_with_lines(uploaded_file):
    uploaded_file.seek(0)
    pages_data = []

    with pdfplumber.open(uploaded_file) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(
                x_tolerance=2,
                y_tolerance=3,
                keep_blank_chars=False,
                use_text_flow=False,
            ) or []

            lines = group_words_into_lines(words)

            pages_data.append({
                "page_number": page_number,
                "width": float(page.width),
                "lines": lines,
            })

    return pages_data


def extract_raw_text(pages_data):
    texts = []
    for page in pages_data:
        for line in page["lines"]:
            texts.append(line["text"])
    return "\n".join(texts)


def find_doc_no(text: str) -> str:
    match = re.search(r"\b(PSI\d+)\b", text, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def find_posting_date(text: str) -> str:
    match = re.search(r"Posting\s+Date\s+(\d{2}/\d{2}/\d{4})", text, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def find_movement_date(text: str) -> str:
    patterns = [
        r"Promised\s+Delivery\s+Date\s+(\d{2}/\d{2}/\d{4})",
        r"Delivery\s+Date\s+(\d{2}/\d{2}/\d{4})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    return ""


def find_customer(lines) -> str:
    for line in lines:
        text = line["text"]
        if re.match(r"^Customer\b", text, re.IGNORECASE):
            value = re.sub(r"^Customer\s*:?\s*", "", text, flags=re.IGNORECASE).strip()
            return value
    return ""


def find_delivery_address(lines) -> str:
    stop_labels = (
        "Contact Name",
        "Contact No.",
        "Phone No.",
        "VAT Reg No.",
        "Liquor License No.",
        "Company Reg No.",
        "Your Reference",
        "Invoice No.",
        "SO No.",
    )

    for i, line in enumerate(lines):
        text = line["text"]

        if re.match(r"^Delivery\s+Address\b", text, re.IGNORECASE):
            first_value = re.sub(
                r"^Delivery\s+Address\s*:?\s*",
                "",
                text,
                flags=re.IGNORECASE
            ).strip()

            parts = []
            if first_value:
                parts.append(first_value)

            for next_line in lines[i + 1:]:
                next_text = next_line["text"].strip()

                if not next_text:
                    break

                if any(next_text.startswith(label) for label in stop_labels):
                    break

                parts.append(next_text)

            return ", ".join(parts)

    return ""


def is_item_header_line(text: str) -> bool:
    t = normalize(text)
    return (
        "code" in t and
        "description" in t and
        "quantity" in t and
        "unit of measure" in t
    )


def is_totals_line(text: str) -> bool:
    t = normalize(text)
    return (
        "total ksh excl" in t or
        "total excl. vat" in t or
        "vat :" in t or
        "total ksh incl" in t
    )


def phrase_span(words, phrase_tokens):
    normalized_words = [normalize(w["text"]) for w in words]
    phrase_len = len(phrase_tokens)

    for i in range(len(normalized_words) - phrase_len + 1):
        if normalized_words[i:i + phrase_len] == list(phrase_tokens):
            matched_words = words[i:i + phrase_len]
            return {
                "x0": float(matched_words[0]["x0"]),
                "x1": float(matched_words[-1]["x1"]),
                "center": (float(matched_words[0]["x0"]) + float(matched_words[-1]["x1"])) / 2,
            }

    return None


def single_word_span(words, token):
    for w in words:
        if normalize(w["text"]) == token:
            return {
                "x0": float(w["x0"]),
                "x1": float(w["x1"]),
                "center": word_center(w),
            }
    return None


def get_header_boundaries(header_words, page_width):
    header_words = sorted(header_words, key=lambda w: w["x0"])

    code = single_word_span(header_words, "code")
    description = single_word_span(header_words, "description")
    quantity = single_word_span(header_words, "quantity")
    unit_of_measure = phrase_span(header_words, ("unit", "of", "measure"))
    unit_price = phrase_span(header_words, ("unit", "price"))

    if not all([code, description, quantity, unit_of_measure]):
        return None

    unit_price_center = unit_price["center"] if unit_price else page_width * 0.78

    boundaries = {
        "code_desc": (code["center"] + description["center"]) / 2,
        "desc_qty": (description["center"] + quantity["center"]) / 2,
        "qty_pack": (quantity["center"] + unit_of_measure["center"]) / 2,
        "pack_price": (unit_of_measure["center"] + unit_price_center) / 2,
    }

    return boundaries


def split_words_into_item_columns(words, boundaries):
    code_words = []
    desc_words = []
    qty_words = []
    pack_words = []

    for word in sorted(words, key=lambda w: w["x0"]):
        x = word_center(word)
        text = clean_text(word["text"])

        if x < boundaries["code_desc"]:
            code_words.append(text)
        elif x < boundaries["desc_qty"]:
            desc_words.append(text)
        elif x < boundaries["qty_pack"]:
            qty_words.append(text)
        elif x < boundaries["pack_price"]:
            pack_words.append(text)

    return {
        "InvoiceCode": " ".join(code_words).strip(),
        "InvoiceDescription": " ".join(desc_words).strip(),
        "QtyCases": " ".join(qty_words).strip(),
        "PackText": " ".join(pack_words).strip(),
    }


def is_numeric_token(token: str) -> bool:
    token = token.strip().replace(",", "")
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", token))


def looks_like_pack_text(text: str) -> bool:
    t = normalize(text)
    return any(marker in t for marker in [" x ", "ml", "ltr", "l ", "litre", "btl", "bottle", "cl"])


def normalize_pack_unit(unit: str) -> str:
    u = unit.strip().lower()

    if u in {"l", "ltr", "litre"}:
        return "L"

    if u in {"ml", "cl"}:
        return u

    return unit.strip()


def format_pack_text(count: str, size: str, unit: str) -> str:
    return f"{count.strip()} x {size.strip()}{normalize_pack_unit(unit)}"


def is_complete_pack_text(text: str) -> bool:
    return bool(PACK_PATTERN.fullmatch(clean_text(text)))


def extract_full_pack_text_from_text(text: str) -> str:
    match = PACK_PATTERN.search(clean_text(text))
    if not match:
        return ""

    count, size, unit = match.groups()
    return format_pack_text(count, size, unit)


def extract_size_only_from_text(text: str) -> str:
    match = SIZE_ONLY_PATTERN.search(clean_text(text))
    if not match:
        return ""

    size, unit = match.groups()
    return f"{size.strip()}{normalize_pack_unit(unit)}"


def refine_item_fields(item: dict) -> dict:
    description = clean_text(item.get("InvoiceDescription", ""))
    qty = clean_text(item.get("QtyCases", ""))
    pack = clean_text(item.get("PackText", ""))
    source_text = clean_text(item.get("_source_text", ""))

    qty_tokens = qty.split()

    if qty_tokens:
        numeric_positions = [i for i, token in enumerate(qty_tokens) if is_numeric_token(token)]
        if numeric_positions:
            last_num_idx = numeric_positions[-1]
            leading_tokens = qty_tokens[:last_num_idx]
            numeric_part = " ".join(qty_tokens[last_num_idx:]).strip()

            if leading_tokens and all(not is_numeric_token(tok) for tok in leading_tokens):
                moved_text = " ".join(leading_tokens).strip()
                description = f"{description} {moved_text}".strip()
                qty = numeric_part

    qty_tokens = qty.split()
    if len(qty_tokens) > 1 and looks_like_pack_text(qty):
        first_numeric = qty_tokens[0] if qty_tokens and is_numeric_token(qty_tokens[0]) else ""
        remaining = " ".join(qty_tokens[1:]).strip()

        if first_numeric and remaining and looks_like_pack_text(remaining):
            qty = first_numeric
            if not pack:
                pack = remaining

    qty_tokens = qty.split()
    if qty_tokens and not all(is_numeric_token(tok) for tok in qty_tokens):
        numeric_only = [tok for tok in qty_tokens if is_numeric_token(tok)]
        non_numeric = [tok for tok in qty_tokens if not is_numeric_token(tok)]

        if numeric_only:
            qty = numeric_only[-1]
            if non_numeric:
                description = f"{description} {' '.join(non_numeric)}".strip()

    if not is_complete_pack_text(pack):
        full_pack_from_line = extract_full_pack_text_from_text(source_text)

        if full_pack_from_line:
            pack = full_pack_from_line
        elif re.fullmatch(r"\d+", pack):
            size_only = extract_size_only_from_text(source_text)
            if size_only:
                pack = f"{pack} x {size_only}"

    item["InvoiceDescription"] = re.sub(r"\s+", " ", description).strip()
    item["QtyCases"] = re.sub(r"\s+", " ", qty).strip()
    item["PackText"] = re.sub(r"\s+", " ", pack).strip()

    return item


def parse_line_items(pages_data):
    items = []

    for page in pages_data:
        lines = page["lines"]
        width = page["width"]

        header_found = False
        current_item = None
        boundaries = None

        for line in lines:
            text = line["text"].strip()

            if not header_found:
                if is_item_header_line(text):
                    header_found = True
                    boundaries = get_header_boundaries(line["words"], width)
                continue

            if is_totals_line(text):
                if current_item and (current_item["InvoiceCode"] or current_item["InvoiceDescription"]):
                    items.append(refine_item_fields(current_item))
                    current_item = None
                break

            if boundaries is None:
                continue

            columns = split_words_into_item_columns(line["words"], boundaries)

            has_code = bool(columns["InvoiceCode"])
            has_desc = bool(columns["InvoiceDescription"])
            has_qty = bool(columns["QtyCases"])
            has_pack = bool(columns["PackText"])

            if not (has_code or has_desc or has_qty or has_pack):
                continue

            if has_code or has_qty or has_pack:
                if current_item and (current_item["InvoiceCode"] or current_item["InvoiceDescription"]):
                    items.append(refine_item_fields(current_item))

                current_item = {
                    "InvoiceCode": columns["InvoiceCode"],
                    "InvoiceDescription": columns["InvoiceDescription"],
                    "QtyCases": columns["QtyCases"],
                    "PackText": columns["PackText"],
                    "_source_text": text,
                }
            else:
                if current_item and has_desc:
                    current_item["InvoiceDescription"] = (
                        f"{current_item['InvoiceDescription']} {columns['InvoiceDescription']}"
                    ).strip()
                    current_item["_source_text"] = (
                        f"{current_item.get('_source_text', '')} {text}"
                    ).strip()

        if current_item and (current_item["InvoiceCode"] or current_item["InvoiceDescription"]):
            candidate = refine_item_fields(current_item)
            if not items or items[-1] != candidate:
                items.append(candidate)

    return items


def build_line_preview(pages_data):
    preview = []
    for page in pages_data:
        preview.append(f"--- PAGE {page['page_number']} ---")
        for line in page["lines"][:40]:
            preview.append(line["text"])
    return preview


def parse_invoice(uploaded_file) -> dict:
    pages_data = extract_pages_with_lines(uploaded_file)
    raw_text = extract_raw_text(pages_data)

    first_page_lines = pages_data[0]["lines"] if pages_data else []

    posted_on = find_posting_date(raw_text)
    movement_date = find_movement_date(raw_text)

    if not movement_date:
        movement_date = posted_on

    data = {
        "DocNo": find_doc_no(raw_text),
        "DocType": "Invoice",
        "MovementDate": movement_date,
        "PostedOn": posted_on,
        "Delivered To": find_delivery_address(first_page_lines),
        "Customer": find_customer(first_page_lines),
        "rows": parse_line_items(pages_data),
        "raw_text": raw_text,
        "tables_preview": [],
        "line_preview": build_line_preview(pages_data),
    }

    return data