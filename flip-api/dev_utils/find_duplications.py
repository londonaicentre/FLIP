# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

# %%
import os
from pathlib import Path

from thefuzz import fuzz

MODULE_PATH = Path(__file__).resolve().parents[1]


def _find_definitions(code_dir, keyword):
    """Find every line containing `keyword` in the .py files under `code_dir`."""
    found = []
    for root, _, files in os.walk(code_dir):
        for file in files:
            if file.endswith(".py"):
                with open(os.path.join(root, file), "r") as f:
                    for line in f:
                        if keyword in line:
                            found.append(f"{str(root).replace(str(MODULE_PATH), '')}{file}\n\t\t\t{line.strip()}")
    return found


def find_function_definitions(code_dir):
    """
    Find all function definitions in the code directory.
    """
    return _find_definitions(code_dir, "def ")


def find_class_definitions(code_dir):
    """
    Find all class definitions in the code directory.
    """
    return _find_definitions(code_dir, "class ")


def _extract_name(definition_str):
    """Name from "def name(...):", "class Name(...):" or "class Name:"; None if neither."""
    if "def " in definition_str:
        rest = definition_str.split("def ")[1]
    elif "class" in definition_str:
        rest = definition_str.split("class ")[1]
    else:
        return None
    return rest.split("(", 1)[0].split(":", 1)[0].strip()


# %%
# Print the function names that appear more than once
def print_duplicate_names(definitions, similarity_threshold=85, item_type="Identifier"):
    """
    Identifies and prints groups of similar names from a list of definitions
    using fuzzy string matching.
    Original function detected exact duplicates of 'def name' or 'class name'.
    This version extracts pure identifiers and finds similarities.
    """
    if not definitions:
        print(f"No {item_type.lower()} definitions provided.")
        return

    identifiers = [name for d in definitions if (name := _extract_name(d))]
    if not identifiers:
        print(f"Could not extract any {item_type.lower()} names from the definitions.")
        return

    # Find groups of similar names
    groups = []
    # Keep track of indices of identifiers that have been assigned to a group
    processed_indices = [False] * len(identifiers)

    for i in range(len(identifiers)):
        if processed_indices[i]:
            continue

        current_identifier = identifiers[i]
        # Start a new group with the current identifier
        # Using a set for current_group automatically handles exact duplicates within a similarity group
        current_group = {current_identifier}

        # Compare current_identifier with all subsequent identifiers
        for j in range(i + 1, len(identifiers)):
            # We don't check processed_indices[j] here because an identifier might be similar
            # to current_identifier even if it later forms its own group or is part of another.
            # The purpose of processed_indices is to ensure each identifier starts a *new* group search only once.

            other_identifier = identifiers[j]

            # Calculate similarity score (0-100)
            score = fuzz.ratio(current_identifier, other_identifier)

            if score >= similarity_threshold:
                current_group.add(other_identifier)

        # If the group has more than one member, it means similar items were found
        if len(current_group) > 1:
            groups.append(sorted(current_group))
            # Mark every identifier in this group as processed so it does not start its own search
            for k, ident in enumerate(identifiers):
                if ident in current_group:
                    processed_indices[k] = True

    if not groups:
        print(f"No significantly similar {item_type.lower()} names found (threshold: {similarity_threshold}%).")
    else:
        print(f"Found groups of potentially similar {item_type.lower()} names (threshold: {similarity_threshold}%):")
        for idx, group in enumerate(groups):
            print(f"\tGroup {idx + 1}:")
            print(f"\t\tCount: {len(group)}")
            print("\t\tFound at file(s):")
            for item in group:
                # Find the original definition line for each item in the group
                for definition_str in definitions:
                    if item in definition_str:
                        print(f"\t\t\t{definition_str}")
                        break


def print_exact_duplicates(definitions, item_type="Identifier"):
    """
    Identifies and prints exact duplicates from a list of definitions.
    """
    if not definitions:
        print(f"No {item_type.lower()} definitions provided.")
        return

    # Create a dictionary to count occurrences of each identifier
    identifier_counts = {}
    origin_file = {}
    for definition_str in definitions:
        name = _extract_name(definition_str)
        if name:  # Only add if a name was successfully extracted
            identifier_counts[name] = identifier_counts.get(name, 0) + 1
            # Store the origin file for each identifier
            origin_file[name] = definition_str.split("\n")[0]

    # Filter out identifiers that appear only once
    duplicates = {name: count for name, count in identifier_counts.items() if count > 1}

    if not duplicates:
        print(f"No exact duplicate {item_type.lower()} names found.")
    else:
        print(f"Found exact duplicate {item_type.lower()} names:")
        for name, count in duplicates.items():
            print(f"\t{name}: {count} times")
            print("\t\tFound at file:")
            print(f"\t\t\t{origin_file[name]}")


if __name__ == "__main__":
    print(f"Module directory: {MODULE_PATH}")
    CODE_DIR = MODULE_PATH / "src/flip_api/"
    print(f"Code directory: {CODE_DIR}")
    function_definitions = find_function_definitions(CODE_DIR)
    class_definitions = find_class_definitions(CODE_DIR)
    print_duplicate_names(function_definitions, similarity_threshold=95)
    print_duplicate_names(class_definitions, similarity_threshold=95)
    print_exact_duplicates(function_definitions)
    print_exact_duplicates(class_definitions)
