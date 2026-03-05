# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

from PyPDFForm import PdfWrapper

def inspect_pdf_fields(pdf_path):
    try:
        # Load the PDF
        with open(pdf_path, "rb") as f:
            filled_pdf = PdfWrapper(f.read())
            
        fields = filled_pdf.schema
        print(f"\n--- Fields found in: {pdf_path} ---")
        
        if 'properties' in fields:
            for field_name in fields['properties']:
                print(f"Field Key: {field_name}")
        else:
            print("No fillable fields detected. This PDF might be 'flattened'.")
            
        print("-----------------------------------\n")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    # The .strip() now removes &, ', and spaces added by PowerShell
    path = input("Drag and drop your PDF here and press Enter: ").strip().replace("& ", "").strip("'\"")
    if path:
        inspect_pdf_fields(path)
        input("Press Enter to close...")