"""Create a synthetic offline example. Every value is invented for this demo."""

import argparse
from pathlib import Path

from openpyxl import Workbook


def create_demo(path: Path) -> None:
    book = Workbook()
    flow = book.active
    flow.title = "Steps"
    flow.append(["Module", "Case ID", "Step Count", "Step Description",
                 "Application", "Expected Result", "External System"])
    flow.append(["STP", "CASE_001", 1, "Review the document", "Demo Viewer",
                 "Status = Draft", None])
    flow.append(["Other", "CASE_001", 2, "Review the attachment", "Demo Archive",
                 "Attachment visible", None])
    flow.append(["STP", "CASE_002", 1, "Send to external approval", "Demo Editor",
                 "Approval request visible", "Example approval service"])
    data = book.create_sheet("Test Data")
    data.append(["Test Data", "Value"])
    data.append(["Document", "DEMO-DOCUMENT"])
    book.save(path)
    book.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output file")
    create_demo(args.output)
