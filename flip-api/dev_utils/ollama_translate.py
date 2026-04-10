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

import ast
import re
import subprocess
import textwrap
import time
from pathlib import Path
from typing import List, Tuple  # Ensure Tuple is imported

import autopep8
import libcst as cst
from libcst.codemod import CodemodContext, VisitorBasedCodemodCommand
from ollama import chat

# Configuration
MODULE_PATH = Path(__file__).resolve().parents[1]  # flip-api/
MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 5
MAX_POST_TRANSLATION_ATTEMPTS = 4  # Maximum correction iterations per file
MODEL_CONTEXT_SIZES = {
    "deepseek-coder:33b": 16384,
    "llama3:70b": 8192,
    "mistral:latest": 32768,
}
MODEL_NAME = "deepseek-coder:33b"
TRANSLATION_DIR = MODULE_PATH / "src/flip_api/translation/"
TEST_DIR = MODULE_PATH / "tests" / "translation"  # Make test dir model-specific too


class CodeTranslator:
    def __init__(self):
        self.setup_directories()

    def setup_directories(self):
        """Create required directories and __init__.py files."""
        TRANSLATION_DIR.mkdir(parents=True, exist_ok=True)
        (TRANSLATION_DIR / "__init__.py").touch(exist_ok=True)

        TEST_DIR.mkdir(parents=True, exist_ok=True)
        (TEST_DIR / "__init__.py").touch(exist_ok=True)

    def _ensure_package_structure(self, base_dir: Path, file_path: Path):
        """Ensure __init__.py files exist from base_dir up to file_path's parent."""
        if not file_path.is_relative_to(base_dir):
            return

        current = file_path.parent
        while current != base_dir.parent and current != base_dir:
            (current / "__init__.py").touch(exist_ok=True)
            if current == current.parent:  # Safety break for root
                break
            current = current.parent
        (base_dir / "__init__.py").touch(exist_ok=True)  # Ensure base __init__.py

    def get_files_to_upload(self, folder_path: str) -> List[Path]:
        """Get TypeScript files excluding test files."""
        return [file for file in Path(folder_path).rglob("*.ts") if file.is_file() and "test" not in str(file).lower()]

    def get_file_content(self, file_path: Path) -> str:
        """Read file content."""
        return file_path.read_text()

    def get_python_code(self, max_length: int = 30000) -> str:
        """Get example Python code for context (used for initial translation)."""
        code = []
        total_length = 0
        src_code_dir = MODULE_PATH / "src"  # Assuming example Python is in 'src'
        if not src_code_dir.exists():
            print(f"Warning: Example Python code directory not found: {src_code_dir}")
            return ""

        for file in src_code_dir.rglob("*.py"):
            if "test" in str(file).lower() or "__init__" in str(file):
                continue
            content = f"\n\n# Example from: {file.name}\n{self.get_file_content(file)}"
            if total_length + len(content) > max_length:
                remaining = max_length - total_length
                code.append(content[:remaining])
                break
            code.append(content)
            total_length += len(content)
        return "".join(code)

    def get_context_size(self, model_name: str) -> int:
        """Get context window size for the model."""
        model_key = model_name.lower().split(":")[0]  # e.g., "deepseek-coder" from "deepseek-coder:33b"
        for name, size in MODEL_CONTEXT_SIZES.items():
            if model_key in name.lower():
                return size
        return 4096  # Default fallback

    def build_prompt(self, python_example: str, ts_code: str) -> str:
        """Construct the translation prompt."""
        context_size = self.get_context_size(MODEL_NAME)
        max_content_length = int(context_size * 0.8 * 3.8)  # Approx chars, leaving buffer

        static_prompt = (
            "Translate the following TypeScript code to Python. Follow these rules:\n"
            "1. Use SQLModel for database models if applicable.\n"
            "2. Convert to FastAPI endpoints where appropriate.\n"
            "3. Adhere to Pythonic OOP best practices and idiomatic Python.\n"
            "4. Avoid async/await unless absolutely necessary for the logic.\n"
            "5. Generate complete, runnable Python code, including necessary imports.\n"
            "6. Ensure all type hints are present for functions (parameters and returns) and variables.\n\n"
            "Reference Python Example (for style and common patterns):\n```python\n{example}\n```\n\n"
            "TypeScript Code to Translate:\n```typescript\n{ts_code}\n```\n\n"
            "Translated Python Code (provide only the complete Python code block):"
        )

        static_length = len(static_prompt.format(example="", ts_code=""))
        available_space = max_content_length - static_length
        if available_space <= 0:
            available_space = 2000  # Fallback

        example_max = min(len(python_example), int(available_space * 0.3))
        ts_max = min(len(ts_code), available_space - example_max)
        ts_max = max(0, ts_max)  # ensure not negative

        return static_prompt.format(example=python_example[:example_max], ts_code=ts_code[:ts_max])

    def build_correction_prompt(self, original_ts_code: str, current_python_code: str, error_messages: str) -> str:
        """Constructs a prompt to ask the LLM to correct Python code based on errors."""
        context_size = self.get_context_size(MODEL_NAME)
        max_content_length = int(context_size * 0.8 * 3.8)  # Approx chars

        prompt_template = (
            "You are an expert code translator and debugger. "
            "An attempt was made to translate TypeScript to Python. The Python code has issues. "
            "Review the original TypeScript, the problematic Python code, and the error messages. "
            "Provide a corrected, complete, and runnable Python version. Only output the Python code block.\n"
            "Focus on fixing the reported errors and ensuring overall code quality and correctness.\n"
            "Ensure all type hints are present and correct.\n\n"
            "Original TypeScript Code:\n```typescript\n{ts_code}\n```\n\n"
            "Current (Problematic) Python Code:\n```python\n{python_code}\n```\n\n"
            "Error Messages/Feedback:\n```text\n{errors}\n```\n\n"
            "Corrected Python Code (provide only the complete Python code block):"
        )

        static_length = len(prompt_template.format(ts_code="", python_code="", errors=""))
        available_space = max_content_length - static_length
        if available_space <= 0:
            available_space = 2000  # Fallback

        errors_len = min(len(error_messages), int(available_space * 0.4))
        python_code_len = min(len(current_python_code), int(available_space * 0.3))
        ts_code_len = min(len(original_ts_code), available_space - errors_len - python_code_len)
        ts_code_len = max(0, ts_code_len)

        return prompt_template.format(
            ts_code=original_ts_code[:ts_code_len],
            python_code=current_python_code[:python_code_len],
            errors=error_messages[:errors_len],
        )

    def translate_code(self, prompt: str) -> str:
        """Execute chat request with retries."""
        retry_delay = INITIAL_RETRY_DELAY
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = chat(
                    model=MODEL_NAME,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are an expert code translator specializing in TypeScript to Python "
                            "conversion. You generate clean, idiomatic, and fully typed Python code.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    options={
                        "temperature": 0.0,
                        "num_ctx": self.get_context_size(MODEL_NAME),
                    },  # Low temp for deterministic output
                )
                return response["message"]["content"]
            except Exception as e:
                if attempt == MAX_RETRIES:
                    print(f"Max retries reached for LLM call. Error: {e}")
                    raise
                print(f"LLM call attempt {attempt + 1} failed. Retrying in {retry_delay}s... Error: {e}")
                time.sleep(retry_delay)
                retry_delay *= 2
        return ""  # Should be unreachable if MAX_RETRIES > 0 due to raise

    def extract_code_blocks(self, response: str) -> str:
        """Extract Python code blocks from response."""
        code_blocks = re.findall(r"```(?:python)?\s*\n(.*?)(?:\n```|$)", response, re.DOTALL)
        if not code_blocks:  # Fallback if no ```python block, assume whole response is code
            # Be cautious with this fallback, it might grab unwanted text.
            # A more robust check could be to see if the response *looks* like Python.
            if "def " in response or "class " in response or "import " in response:
                return response.strip()
            return ""  # Return empty if no blocks and doesn't look like code
        return "\n".join([block.strip() for block in code_blocks])

    def write_python_code(self, file_path: Path, response: str):
        """Write extracted Python code to file."""
        code = self.extract_code_blocks(response)
        if not code and response.strip():  # If extraction failed but response has content
            print(
                f"Warning: Could not extract Python code block from LLM response for {file_path.name}. "
                "Writing raw response."
            )
            code = response.strip()  # Write the raw response as a last resort
        elif not code and not response.strip():
            print(f"Warning: LLM response for {file_path.name} was empty. Not writing file.")
            return  # Do not write an empty file if response was empty

        file_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_package_structure(TRANSLATION_DIR, file_path)
        file_path.write_text(code)

    def fix_type_issues_for_file(self, py_file: Path):
        """Automatically add basic '-> None' return type hints to a single Python file."""
        if not py_file.exists() or py_file.stat().st_size == 0:
            print(f"File {py_file} not found or empty for type fixing.")
            return

        class ReturnNoneAnnotator(VisitorBasedCodemodCommand):
            def leave_FunctionDef(
                self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
            ) -> cst.FunctionDef:
                if not updated_node.returns:  # Add '-> None' if no return type is specified
                    return updated_node.with_changes(returns=cst.Annotation(cst.Name("None")))
                return updated_node

        try:
            code = py_file.read_text()
            if not code.strip():  # Skip if file is effectively empty
                print(f"Skipping type fixing for effectively empty file: {py_file.name}")
                return
            module_cst = cst.parse_module(code)
            context = CodemodContext()
            transformed_module = ReturnNoneAnnotator(context).transform_module(module_cst)

            if transformed_module.code != code:
                py_file.write_text(transformed_module.code)
                print(f"Applied '-> None' type hints to {py_file.name}")
        except cst.ParserSyntaxError as e:
            msg = f"CST Parsing error in {py_file.name} during type fixing: {e}. This error will be reported to LLM."
            print(msg)
            raise ValueError(msg) from e  # Re-raise as a standard exception type for the loop
        except Exception as e:
            msg = f"Error during type fixing for {py_file.name}: {e}"
            print(msg)
            raise ValueError(msg) from e

    def run_type_checking_on_file(self, file_path: Path) -> Tuple[bool, str]:
        """Run mypy static type checking on a single file."""
        if not file_path.exists() or file_path.stat().st_size == 0:
            return True, f"File not found or empty, skipping type checking: {file_path}"
        try:
            cmd = ["mypy", str(file_path.relative_to(MODULE_PATH)), "--ignore-missing-imports", "--check-untyped-defs"]
            process = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=MODULE_PATH)
            if process.returncode != 0:
                return False, f"Mypy errors in {file_path.name}:\n{process.stdout}\n{process.stderr}".strip()
            return True, ""
        except Exception as e:
            return False, f"Mypy execution failed for {file_path.name}: {str(e)}"

    def run_linting_on_file(self, file_path: Path) -> Tuple[bool, str]:
        """Run autopep8 and pylint on a single file."""
        if not file_path.exists() or file_path.stat().st_size == 0:
            return True, f"File not found or empty, skipping linting: {file_path}"

        all_lint_issues = []
        try:
            # autopep8
            original_code = file_path.read_text()
            if original_code.strip():  # Only run if not empty
                fixed_code = autopep8.fix_file(str(file_path), options={"aggressive": 2, "experimental": True})
                if fixed_code != original_code:
                    file_path.write_text(fixed_code)
                    print(f"Applied autopep8 fixes to {file_path.name}")

            # Pylint
            # Common disables: C0114,C0115,C0116 (missing-docstring), R0903 (too-few-public), W0511 (fixme)
            # R0913 (too-many-arguments), W0611 (unused-import) can be useful for generated code.
            disable_checks = "C0114,C0115,C0116,R0903,W0511"
            cmd = ["pylint", f"--disable={disable_checks}", str(file_path.relative_to(MODULE_PATH))]
            process = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=MODULE_PATH)

            # Pylint exit codes: 0=ok, 1=fatal, 2=error, 4=warning, 8=refactor, 16=convention
            # Consider fatal, error, warning as significant.
            is_failure = bool(process.returncode & (1 | 2 | 4))
            if process.returncode != 0:
                pylint_output = (
                    f"Pylint issues in {file_path.name} (exit code {process.returncode}):\n{process.stdout}".strip()
                )
                all_lint_issues.append(pylint_output)
                if is_failure:
                    return False, "\n".join(all_lint_issues)
                else:  # Minor issues (refactor/convention)
                    print(pylint_output)  # Log but don't fail the step for these

            return True, ""  # Passed if no critical pylint issues
        except Exception as e:
            return False, f"Linting execution failed for {file_path.name}: {str(e)}\n" + "\n".join(all_lint_issues)

    def generate_tests_for_file(self, src_file: Path) -> Path | None:
        """Generate pytest test cases for a single Python source file. Returns test file path or None."""
        if not src_file.exists() or src_file.stat().st_size == 0:
            print(f"Source file {src_file} not found or empty for test generation.")
            return None

        class TestGenerator(ast.NodeVisitor):
            def __init__(self, module_import_path: str):
                self.test_cases: List[str] = []
                self.imports: set[str] = {f"from {module_import_path} import *"}  # Basic import all

            def visit_ClassDef(self, node: ast.ClassDef):
                if not node.name.startswith("_"):
                    # self.imports.add(f"from {self.module_import_path} import {node.name}")
                    test_methods = []
                    for item in node.body:
                        if (
                            isinstance(item, ast.FunctionDef)
                            and not item.name.startswith("_")
                            and item.name != "__init__"
                        ):
                            test_methods.append(
                                textwrap.dedent(f"""
    def test_{item.name}(self, instance):
        # TODO: Implement test for {node.name}.{item.name}
        assert hasattr(instance, '{item.name}')""")
                            )

                    if test_methods or True:  # Always generate class test if class exists
                        class_test = textwrap.dedent(f"""
class Test{node.name}:
    @pytest.fixture
    def instance(self):
        # TODO: Adjust instantiation if '{node.name}' has required __init__ args
        return {node.name}()
""") + "\n".join(test_methods)
                        self.test_cases.append(class_test)
                self.generic_visit(node)

            def visit_FunctionDef(self, node: ast.FunctionDef):
                # Check if it's a top-level function (not part of a class)
                is_top_level = not any(
                    isinstance(ancestor, ast.ClassDef) for ancestor in getattr(node, "ancestors", [])
                )
                if is_top_level and not node.name.startswith("_"):
                    # self.imports.add(f"from {self.module_import_path} import {node.name}")
                    self.test_cases.append(
                        textwrap.dedent(f"""
def test_{node.name}():
    # TODO: Implement test for top-level function {node.name}
    # Example: result = {node.name}(...)
    # assert result == expected_value
    pass  # Placeholder
""")
                    )
                self.generic_visit(node)

        try:
            code = src_file.read_text()
            if not code.strip():
                return None  # Skip empty files

            tree = ast.parse(code, filename=src_file.name)
            # Add parent references to tree nodes for context in visitor
            for node_obj in ast.walk(tree):
                for child in ast.iter_child_nodes(node_obj):
                    setattr(child, "ancestors", getattr(node_obj, "ancestors", []) + [node_obj])

            # Module import path: e.g., translation.model_name_folder.subdir.filename
            relative_to_module_path = src_file.relative_to(MODULE_PATH)
            module_import_str = str(relative_to_module_path.with_suffix("")).replace("/", ".")

            generator = TestGenerator(module_import_path=module_import_str)
            generator.visit(tree)

            if not generator.test_cases:
                print(f"No testable units found in {src_file.name}, no tests generated.")
                return None

            import_statements = "\n".join(sorted(list(generator.imports)))
            test_content = f"import pytest\n{import_statements}\n\n" + "\n\n".join(generator.test_cases)

            relative_src_path = src_file.relative_to(TRANSLATION_DIR)
            test_file = TEST_DIR / relative_src_path.parent / f"test_{src_file.name}"

            test_file.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_package_structure(TEST_DIR, test_file)
            test_file.write_text(test_content)
            print(f"Generated tests for {src_file.name} at {test_file}")
            return test_file
        except SyntaxError as e:
            msg = f"Syntax error in {src_file.name} during test generation: {e}"
            print(msg)
            raise ValueError(msg) from e
        except Exception as e:
            msg = f"Error during test generation for {src_file.name}: {e}"
            print(msg)
            raise ValueError(msg) from e

    def run_tests_for_file(self, test_file_path: Path | None) -> Tuple[bool, str]:
        """Execute pytest on a single test file."""
        if not test_file_path or not test_file_path.exists() or test_file_path.stat().st_size == 0:
            return True, f"Test file not found, empty, or not specified, skipping test run: {test_file_path}"
        try:
            # Run pytest from MODULE_PATH (project root)
            cmd = ["pytest", str(test_file_path.relative_to(MODULE_PATH)), "-v", "--color=yes"]
            process = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=MODULE_PATH)
            output = f"{process.stdout}\n{process.stderr}".strip()

            # Pytest exit codes: 0=ok, 1=tests failed, 2=interrupted, 5=no tests collected
            if process.returncode == 0:  # All tests passed
                return True, f"Pytest passed for {test_file_path.name}:\n{output}"
            if process.returncode == 5:  # No tests collected
                print(
                    f"No tests collected by pytest for {test_file_path.name}. Considering as passed for this iteration."
                )
                return True, f"Pytest: No tests collected for {test_file_path.name}.\n{output}"
            # Any other non-zero exit code is a failure
            return False, f"Pytest failures for {test_file_path.name} (exit code {process.returncode}):\n{output}"
        except Exception as e:
            return False, f"Pytest execution failed for {test_file_path.name}: {str(e)}"

    def process_translation(self, ts_folder_path_str: str):
        """Full translation pipeline with iterative correction for each file."""
        python_examples = self.get_python_code()
        ts_folder = Path(ts_folder_path_str)

        for ts_file in self.get_files_to_upload(ts_folder_path_str):
            # Determine relative path from input ts_folder to ts_file
            try:
                relative_ts_path = ts_file.relative_to(ts_folder)
            except ValueError:  # ts_file not in ts_folder, use ts_file.name
                relative_ts_path = Path(ts_file.name)

            output_py_file = TRANSLATION_DIR / relative_ts_path.with_suffix(".py")
            output_py_file.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_package_structure(TRANSLATION_DIR, output_py_file)

            original_ts_code = self.get_file_content(ts_file)

            if not output_py_file.exists() or output_py_file.stat().st_size == 0:
                print(f"\nTranslating {ts_file.name} to {output_py_file.name}...")
                initial_prompt = self.build_prompt(python_examples, original_ts_code)
                try:
                    response_content = self.translate_code(initial_prompt)
                    self.write_python_code(output_py_file, response_content)
                    if output_py_file.exists() and output_py_file.stat().st_size > 0:
                        print(f"Saved initial translation: {output_py_file}")
                    else:
                        print(
                            f"Warning: Initial translation for {output_py_file.name} resulted in an empty file or "
                            "failed write."
                        )
                        # Potentially skip correction loop if initial translation failed badly
                        # continue
                except Exception as e:
                    print(f"Failed initial translation for {ts_file.name}: {str(e)}")
                    continue
            else:
                print(f"\nFile {output_py_file} already exists. Attempting to verify and correct.")

            # Iterative Correction Loop
            passed_all_checks_final = False
            for attempt in range(MAX_POST_TRANSLATION_ATTEMPTS):
                print(
                    f"\n--- Correction Iteration {attempt + 1}/{MAX_POST_TRANSLATION_ATTEMPTS} for "
                    "{output_py_file.name} ---"
                )

                if not output_py_file.exists() or output_py_file.stat().st_size == 0:
                    print(
                        f"Error: Python file {output_py_file.name} is missing or empty before correction step. "
                        "Skipping."
                    )
                    break

                self.get_file_content(output_py_file)
                iteration_errors: List[str] = []
                passed_current_iteration_checks = True

                # 1. Automated fixes (e.g., type hints)
                try:
                    print("Running automated type hint additions...")
                    self.fix_type_issues_for_file(output_py_file)
                except Exception as e:
                    iteration_errors.append(f"Automated type fixing failed: {str(e)}")
                    passed_current_iteration_checks = False

                # 2. Type Checking
                print("Running type checking...")
                type_ok, type_err = self.run_type_checking_on_file(output_py_file)
                if not type_ok:
                    iteration_errors.append(type_err)
                    passed_current_iteration_checks = False

                # 3. Linting (includes autopep8)
                print("Running linting...")
                lint_ok, lint_err = self.run_linting_on_file(output_py_file)
                if not lint_ok:
                    iteration_errors.append(lint_err)
                    passed_current_iteration_checks = False

                # 4. Generate Tests
                test_py_file_path = None
                print("Generating tests...")
                try:
                    test_py_file_path = self.generate_tests_for_file(output_py_file)
                except Exception as e:
                    iteration_errors.append(f"Test generation failed: {str(e)}")
                    passed_current_iteration_checks = False

                # 5. Run Tests
                if test_py_file_path:
                    print(f"Running tests ({test_py_file_path.name})...")
                    tests_ok, test_err = self.run_tests_for_file(test_py_file_path)
                    if not tests_ok:
                        iteration_errors.append(test_err)
                        passed_current_iteration_checks = False
                else:
                    print(f"Skipping test run as no test file was generated for {output_py_file.name}.")
                    # Optionally consider this a failure if tests were expected
                    # iteration_errors.append("Warning: No tests were generated.")
                    # passed_current_iteration_checks = False

                if passed_current_iteration_checks:
                    print(f"All checks passed for {output_py_file.name} in iteration {attempt + 1}.")
                    passed_all_checks_final = True
                    break

                # If errors found and not max attempts:
                print(f"Errors found for {output_py_file.name}:")
                for err in iteration_errors:
                    print(f"  - {err.splitlines()[0]}")  # Print first line of each error

                if attempt < MAX_POST_TRANSLATION_ATTEMPTS - 1:
                    print(f"Attempting LLM correction for {output_py_file.name}...")
                    current_py_code_for_prompt = self.get_file_content(output_py_file)  # Get latest version
                    correction_prompt = self.build_correction_prompt(
                        original_ts_code, current_py_code_for_prompt, "\n---\n".join(iteration_errors)
                    )
                    try:
                        corrected_response_content = self.translate_code(correction_prompt)
                        self.write_python_code(output_py_file, corrected_response_content)
                        if not output_py_file.exists() or output_py_file.stat().st_size == 0:
                            print(
                                f"Warning: LLM correction for {output_py_file.name} resulted in an empty file. "
                                "Stopping corrections for this file."
                            )
                            break  # Stop if LLM erases the file
                        print(f"Applied LLM correction to {output_py_file.name}. Will re-verify.")
                    except Exception as e:
                        print(f"LLM correction attempt failed for {output_py_file.name}: {str(e)}")
                        break  # Stop corrections for this file on LLM error
                else:
                    print(
                        f"Max correction attempts ({MAX_POST_TRANSLATION_ATTEMPTS}) reached for {output_py_file.name}."
                    )
                    if iteration_errors:
                        print(
                            f"Final unresolved errors for {output_py_file.name}:\n"
                            + "\n".join([f"  - {e}" for e in iteration_errors])
                        )

            if passed_all_checks_final:
                print(f"Successfully processed and verified: {output_py_file.name}")
            else:
                print(f"Failed to fully correct {output_py_file.name} after {MAX_POST_TRANSLATION_ATTEMPTS} attempts.")

        print("\n--- All TypeScript files processed. ---")


if __name__ == "__main__":
    translator = CodeTranslator()
    # Example: translate files from a 'typescript_sources' directory relative to this script's location
    # You might need to adjust this path.
    ts_input_dir = Path(__file__).parents[1] / "translate/"  # Assuming 'translate' dir in same location as script
    if not ts_input_dir.exists():
        ts_input_dir.mkdir(parents=True)
        print(f"Created sample input directory: {ts_input_dir}")
        print(f"Please place TypeScript files in {ts_input_dir} to translate.")
    else:
        translator.process_translation(str(ts_input_dir))
