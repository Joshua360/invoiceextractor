import io
from datetime import datetime

import pandas as pd
import streamlit as st

from parser_invoice import parse_invoice

st.set_page_config(page_title="Invoice Extractor", layout="wide")

st.title("Invoice Extractor")
st.caption("Upload one invoice PDF at a time. Add an optional LPO PDF if needed.")

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


def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Invoices")
    output.seek(0)
    return output.getvalue()


def is_valid_date_format(value: str) -> bool:
    try:
        datetime.strptime(value.strip(), "%d/%m/%Y")
        return True
    except ValueError:
        return False


if "master_df" not in st.session_state:
    st.session_state.master_df = pd.DataFrame(columns=OUTPUT_COLUMNS)

if "last_action_message" not in st.session_state:
    st.session_state.last_action_message = ""

if "last_invoice_preview" not in st.session_state:
    st.session_state.last_invoice_preview = None

col1, col2 = st.columns(2)

with col1:
    invoice_file = st.file_uploader(
        "Upload Invoice PDF",
        type=["pdf"],
        key="invoice_uploader",
    )

with col2:
    lpo_file = st.file_uploader(
        "Upload LPO PDF (optional)",
        type=["pdf"],
        key="lpo_uploader",
    )

manual_movement_date = st.text_input(
    "Manual Movement Date (optional)",
    placeholder="DD/MM/YYYY",
    help="If entered, this will be used as MovementDate. Format must be DD/MM/YYYY.",
)

manual_delivered_to = st.text_input(
    "Manual Delivered To (optional)",
    placeholder="Enter Delivered To value",
    help="If entered, this will be used as Delivered To.",
)

button_col1, button_col2 = st.columns(2)

with button_col1:
    process_clicked = st.button("Process Documents", use_container_width=True)

with button_col2:
    reset_clicked = st.button("Reset Batch", use_container_width=True)

if process_clicked:
    if invoice_file is None:
        st.session_state.last_action_message = "Please upload an Invoice PDF first."
    else:
        entered_date = manual_movement_date.strip()
        entered_delivered_to = manual_delivered_to.strip()

        if entered_date and not is_valid_date_format(entered_date):
            st.session_state.last_action_message = (
                "Invalid manual Movement Date format. Please use DD/MM/YYYY."
            )
        else:
            invoice_data = parse_invoice(invoice_file)

            final_movement_date = entered_date if entered_date else invoice_data.get("MovementDate", "")
            final_delivered_to = (
                entered_delivered_to if entered_delivered_to else invoice_data.get("Delivered To", "")
            )

            invoice_data["MovementDate"] = final_movement_date
            invoice_data["Delivered To"] = final_delivered_to

            st.session_state.last_invoice_preview = invoice_data

            if not invoice_data["rows"]:
                st.session_state.last_action_message = (
                    "No line items were extracted from the invoice. "
                    "Check the debug sections below."
                )
            else:
                new_rows = []

                for item in invoice_data["rows"]:
                    invoice_data_row = {
                        "DocNo": invoice_data["DocNo"],
                        "DocType": invoice_data["DocType"],
                        "MovementDate": invoice_data["MovementDate"],
                        "PostedOn": invoice_data["PostedOn"],
                        "Delivered To": invoice_data["Delivered To"],
                        "InvoiceCode": item["InvoiceCode"],
                        "InvoiceDescription": item["InvoiceDescription"],
                        "QtyCases": item["QtyCases"],
                        "PackText": item["PackText"],
                        "Customer": invoice_data["Customer"],
                    }
                    new_rows.append(invoice_data_row)

                new_df = pd.DataFrame(new_rows, columns=OUTPUT_COLUMNS)
                st.session_state.master_df = pd.concat(
                    [st.session_state.master_df, new_df],
                    ignore_index=True,
                )

                message_parts = [
                    f"Processed invoice: {invoice_file.name}",
                    f"Rows added: {len(new_rows)}",
                ]

                if entered_date:
                    message_parts.append(f"MovementDate set manually to {entered_date}")

                if entered_delivered_to:
                    message_parts.append(f"Delivered To set manually to {entered_delivered_to}")

                st.session_state.last_action_message = " | ".join(message_parts)

if reset_clicked:
    st.session_state.master_df = pd.DataFrame(columns=OUTPUT_COLUMNS)
    st.session_state.last_action_message = "Batch cleared."
    st.session_state.last_invoice_preview = None

if st.session_state.last_action_message:
    st.info(st.session_state.last_action_message)

if st.session_state.last_invoice_preview is not None:
    preview = st.session_state.last_invoice_preview

    st.subheader("Latest Extracted Header Fields")
    preview_cols = st.columns(5)
    preview_cols[0].write(f"**DocNo:** {preview.get('DocNo', '')}")
    preview_cols[1].write(f"**DocType:** {preview.get('DocType', '')}")
    preview_cols[2].write(f"**MovementDate:** {preview.get('MovementDate', '')}")
    preview_cols[3].write(f"**PostedOn:** {preview.get('PostedOn', '')}")
    preview_cols[4].write(f"**Customer:** {preview.get('Customer', '')}")

    st.write(f"**Delivered To:** {preview.get('Delivered To', '')}")
    st.write(f"**Extracted line items:** {len(preview.get('rows', []))}")

    with st.expander("Debug: Raw text preview"):
        raw_text = preview.get("raw_text", "")
        st.text(raw_text[:4000] if raw_text else "")

    with st.expander("Debug: Detected tables preview"):
        tables_preview = preview.get("tables_preview", [])
        if not tables_preview:
            st.write("No tables detected.")
        else:
            for table_info in tables_preview:
                st.write(
                    f"**Table {table_info['table_number']}** "
                    f"(rows: {table_info['row_count']})"
                )
                st.write(table_info["rows_preview"])

    with st.expander("Debug: Line preview"):
        for line in preview.get("line_preview", []):
            st.text(line)

st.subheader("Current Batch Table")
st.dataframe(st.session_state.master_df, use_container_width=True)

st.subheader("Batch Summary")
st.write(f"Rows in current batch: {len(st.session_state.master_df)}")

if not st.session_state.master_df.empty:
    excel_bytes = dataframe_to_excel_bytes(st.session_state.master_df)
    st.download_button(
        label="Download Excel",
        data=excel_bytes,
        file_name="invoice_batch.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )