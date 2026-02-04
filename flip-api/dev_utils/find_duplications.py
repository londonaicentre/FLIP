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


def find_function_definitions(code_dir):
    """
    Find all function definitions in the code directory.
    """
    function_definitions = []
    for root, _, files in os.walk(code_dir):
        for file in files:
            if file.endswith(".py"):
                with open(os.path.join(root, file), "r") as f:
                    lines = f.readlines()
                    for line in lines:
                        if "def " in line:
                            line = f"{str(root).replace(str(MODULE_PATH), '')}{file}\n\t\t\t{line.strip()}"
                            function_definitions.append(line)
    return function_definitions


def find_class_definitions(code_dir):
    """
    Find all class definitions in the code directory.
    """
    class_definitions = []
    for root, _, files in os.walk(code_dir):
        for file in files:
            if file.endswith(".py"):
                with open(os.path.join(root, file), "r") as f:
                    lines = f.readlines()
                    for line in lines:
                        if "class " in line:
                            line = f"{str(root).replace(str(MODULE_PATH), '')}{file}\n\t\t\t{line.strip()}"
                            class_definitions.append(line)
    return class_definitions


# %%
# Print the function names that appear more than once
def print_duplicate_names(definitions, similarity_threshold=85, item_type="Identifier", suffix=""):
    """
    Identifies and prints groups of similar names from a list of definitions
    using fuzzy string matching.
    Original function detected exact duplicates of 'def name' or 'class name'.
    This version extracts pure identifiers and finds similarities.
    """
    if not definitions:
        print(f"No {item_type.lower()} definitions provided.")
        return

    identifiers = []
    for definition_str in definitions:
        name = None
        if "def " in definition_str:
            clean_def = definition_str.split("def ")[1]
            # Extract name from "def function_name(params...):"
            name = clean_def.split("(", 1)[0].strip()
        elif "class" in definition_str:
            clean_def = definition_str.split("class ")[1]
            # Extract name from "class ClassName(bases...):" or "class ClassName:"
            name = clean_def.split("(", 1)[0].split(":", 1)[0].strip()

        if name:  # Only add if a name was successfully extracted
            identifiers.append(name)

    if not identifiers:
        print(f"Could not extract any {item_type.lower()} names from the definitions.")
        return

    # Filter out any empty strings that might have slipped through, though unlikely
    identifiers = [ident for ident in identifiers if ident]
    if not identifiers:  # Re-check after filtering
        print(f"No valid {item_type.lower()} names found after extraction.")
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
            groups.append(sorted(list(current_group)))  # Store as a sorted list
            # Mark all identifiers that are part of this newly found group as processed
            for item_in_group in current_group:
                for k_idx, k_val in enumerate(identifiers):
                    if k_val == item_in_group:
                        processed_indices[k_idx] = True
        else:
            # If no similar items were found for current_identifier, mark only itself as processed
            processed_indices[i] = True

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


def print_exact_duplicates(definitions, item_type="Identifier", suffix=""):
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
        name = None
        if "def " in definition_str:
            clean_def = definition_str.split("def ")[1]
            # Extract name from "def function_name(params...):"
            name = clean_def.split("(", 1)[0].split(":", 1)[0].strip()
        elif "class" in definition_str:
            clean_def = definition_str.split("class ")[1]
            # Extract name from "class ClassName(bases...):" or "class ClassName:"
            name = clean_def.split("(", 1)[0].split(":", 1)[0].strip()

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
    # Set the directory to the current working directory

    MODULE_DIR = Path(__file__).resolve().parents[1]
    print(f"Module directory: {MODULE_DIR}")
    CODE_DIR = MODULE_DIR / "src/flip_api/"
    print(f"Code directory: {CODE_DIR}")
    function_definitions = find_function_definitions(CODE_DIR)
    class_definitions = find_class_definitions(CODE_DIR)
    print_duplicate_names(function_definitions, suffix="def", similarity_threshold=95)
    print_duplicate_names(class_definitions, suffix="class", similarity_threshold=95)
    print_exact_duplicates(function_definitions, suffix="def")
    print_exact_duplicates(class_definitions, suffix="class")
