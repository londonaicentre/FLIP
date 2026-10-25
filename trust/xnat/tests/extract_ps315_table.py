# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Refresh ``ps315_table_e1_1.csv`` from the DICOM standard's DocBook source.

Table E.1-1 of PS3.15 lists every attribute the Basic Application Level Confidentiality Profile acts
on, with the action (``X`` remove, ``Z`` empty, ``D`` dummy, ``U`` replace UID, or a choice of those).
``test_anon_script_static.py`` checks the anonymisation script against that list, so a new edition of
the standard that adds rows fails the tests until the script handles them.

Usage (needs network, not run in CI)::

    curl -sSfLO https://dicom.nema.org/medical/dicom/current/source/docbook/part15/part15.xml
    uv run python extract_ps315_table.py part15.xml > ps315_table_e1_1.csv
"""

import csv
import re
import sys
import xml.etree.ElementTree as ET

_DOCBOOK = "{http://docbook.org/ns/docbook}"
_XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
_BASIC_PROFILE_COLUMN = 4


def _text(cell: ET.Element) -> str:
    return " ".join("".join(cell.itertext()).split())


def main(path: str) -> None:
    root = ET.parse(path).getroot()
    with open(path, encoding="utf-8") as source:
        edition = re.search(r"PS3\.15 \d{4}[a-e]", source.read())
    table = next(t for t in root.iter(f"{_DOCBOOK}table") if t.get(_XML_ID) == "table_E.1-1")
    out = csv.writer(sys.stdout, lineterminator="\n")
    sys.stdout.write(f"# DICOM {edition.group(0) if edition else 'PS3.15'}, Table E.1-1 (Basic Profile column)\n")
    out.writerow(["tag", "action", "name"])
    for row in table.iter(f"{_DOCBOOK}tr"):
        cells = [_text(td) for td in row.findall(f"{_DOCBOOK}td")]
        if cells:
            out.writerow([cells[1], cells[_BASIC_PROFILE_COLUMN], cells[0]])


if __name__ == "__main__":
    main(sys.argv[1])
