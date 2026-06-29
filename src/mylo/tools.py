import os
import tempfile
from pygments.lexers import get_lexer_for_filename
from pygments.util import ClassNotFound
from tree_sitter import Parser
import tree_sitter_language_pack as ts_pack
import json
import ast
import base64
from dotenv import load_dotenv
import requests

from pathlib import Path

from langchain_core.tools import tool





"""
You can add more language support to the AST based functions by adding the nesessary node name of the language
in the dicts FUNCTION_NODE_TYPES and LANGUAGE_CLASS_NODE_TYPES. If the language is class based (like java and csharp),
add the language name in the CLASS_BASED_LANGUAGES list. If this doesnt make the function support the language, then
you can add language specifc conditions in the AST based functions


Add more AST based functions by defining it in top level and add the function name and its purpose (also whether it needs extra arguments other than filepath) in the docstring of with_temp_file
function and add the function name in the func_map dictionary inside it. If the function doesnt require any extra arguments, add the function name in the no_arg_funcs dictionary below the func_map dictionary
"""


# Config file path

config_dir = Path.home() / "mylo-config"
ENV_FILE = str(config_dir/ ".env")


def get_language(file_path):
    """
    Identifies the programming language name based on the file extension
    using Pygments. Works with absolute, root, or temp paths.
    """
    try:
        # Extract the filename from the path (handles any path type)
        filename = os.path.basename(file_path)
        
        # get the lexer
        lexer = get_lexer_for_filename(filename)
        
        # 'lexer.aliases[0]' gives the short name (e.g., 'python', 'json')
        return lexer.aliases[0]

    except ClassNotFound:
        return "Unknown Language"
    except Exception as e:
        return f"Error: {str(e)}"
    

# Generate AST from file_path

def generate_ast(file_path):
    """
    Uses Pygments to identify the language and use
    tree-sitter-language-pack to generate the AST.
    """
    try:
        
        # Detects the programming langugage
        lang_alias = get_language(file_path)

        # Get the parser for the specific language
        language = ts_pack.get_language(lang_alias)
        
        # 3. Setup the Parser
        parser = Parser(language)

        # 4. Read file and parse

        with open(file_path, 'rb') as f:
            content = f.read()

        tree = parser.parse(content)
        return tree.root_node

    except ClassNotFound:
        raise ValueError(f"Pygments doesn't recognize the extension for: {file_path}")
    except Exception as e:
        raise RuntimeError(f"Tree-sitter error: {str(e)}")
    


#capture the function definition in AST (Only top level function is recognised now)

FUNCTION_NODE_TYPES = {
    
    "python":     "function_definition",
    "javascript": "function_declaration",
    "typescript": "function_declaration",
    "php":        "function_definition",
    "ruby":       "method",

    
    "cpp":        "function_definition",
    "c":          "function_definition",
    "rust":       "function_item",
    "go":         "function_declaration",
    "swift":      "function_declaration",
    "java":       "method_declaration",   
    "csharp":     "method_declaration",
    "kotlin":     "function_declaration",

    
    "scala":      "function_definition",
    "lua":        "function_declaration",
    "dart":       "function_signature",
}

# List of Object-Oriented languages where "top level" means the immediate methods of the primary class
CLASS_BASED_LANGUAGES = ["java","csharp"]



def find_function_definitions(target_function_name, file_path):
    """
    Finds and returns the complete source code definition of a top-level function using the function name, as a string
    """

    # Detects the language and lowers the alias string
    raw_alias = get_language(file_path).lower()

    # Pygments sometimes returns 'c++' but Tree-sitter strictly expects 'cpp'
    lang_alias = "cpp" if raw_alias in ["cpp", "c++"] else raw_alias

    # Check if the language exist in the FUNCTION_NODE_TYPES (This are the languages that currently supported)
    if lang_alias not in FUNCTION_NODE_TYPES:
        print(f"Language '{lang_alias}' not supported.")
        return None

    try:
        # Generate the AST
        root_node = generate_ast(file_path)

        # Read raw bytes. Tree-sitter uses byte offsets, so we need bytes to slice the exact source code later.
        with open(file_path, 'rb') as f:
            source_bytes = f.read()
    except Exception as e:
        print(f"Error reading file/AST: {e}")
        return None

    # Helper function to convert an AST node back into readable source code string
    def get_text(node):
        if not node:
            return ""
        
        # Slice the byte array using the node's start and end byte positions, then decode to string
        return source_bytes[node.start_byte:node.end_byte].decode('utf-8')

    # Standardize target_types to always be a list/set for uniform evaluation
    raw_target = FUNCTION_NODE_TYPES[lang_alias]
    target_types = raw_target if isinstance(raw_target, list) else [raw_target]


    # ---- LAYER 1 BOUNDARY ISOLATION ----
    # Decide WHERE in the tree we are searching (global scope vs. inside a class)
    nodes_to_check = root_node.children
    if lang_alias in CLASS_BASED_LANGUAGES:

        # If it's a class-based language, find the very first class definition
        for child in root_node.children:
            if child.type == "class_declaration":

                # Go into the body field of that class
                body = child.child_by_field_name("body")
                if body:

                    # We ONLY look at the children inside this first class, ignoring global functions
                    nodes_to_check = body.children
                break # Stop after finding the first class 


    # ---- LAYER 2 EVALUATION ----
    # Iterate through the determined scope to find the function
    for child in nodes_to_check:

        # Check if the current node is a function/method definition for this specific language
        if child.type in target_types:
            
            # STRATEGY 1: Standard Field Matching
            # Most languages cleanly put the function name in a "name" field
            name_node = child.child_by_field_name("name")
            
            # STRATEGY 2: C/C++ Declarator Unroller Fallback
            # C/C++ function signatures can be complex (e.g., `int* foo(int x)`). 
            # The name isn't in "name", it's buried inside a nested "declarator" tree.
            if not name_node and lang_alias in ["cpp", "c"]:
                declarator = child.child_by_field_name("declarator")
                if declarator:
                    curr = declarator

                    # Traverse down the declarator chain until we hit the actual identifier
                    while curr:
                        if curr.type in ["identifier", "field_identifier"]:
                            name_node = curr
                            break
                        sub_name = curr.child_by_field_name("declarator")
                        if sub_name:
                            curr = sub_name
                        else:

                            # Check the direct children of the current declarator
                            for sub in curr.children:
                                if sub.type in ["identifier", "field_identifier"]:
                                    name_node = sub
                                    break
                            break
            
            # STRATEGY 3: Generic Fallback
            # If standard fields fail, just look for the first identifier node inside the function definition
            if not name_node:
                for sub in child.children:
                    if sub.type in ["identifier", "simple_identifier"]:
                        name_node = sub
                        break

            # If we successfully extracted a name and it matches our target, return the full source code
            if name_node and get_text(name_node) == target_function_name:
                return get_text(child)
    
    # If the loop finishes without returning or the function wasn't found in the searched scope, print out
    # function not found at surface level and return none
    print(f"Function '{target_function_name}' not found at the surface level.")
    return None



# captures the function names in AST (only top level function is recognised)


def find_function_names(file_path):
    """
    Returns a list of all Top-level function names defined in the file.
    """

    # Detect the language and lower the alias string
    raw_alias = get_language(file_path).lower()

    # Pygments sometimes returns 'c++', but Tree-sitter strictly expects 'cpp'
    lang_alias = "cpp" if raw_alias in ["cpp", "c++"] else raw_alias

    # Check if the language exist in the FUNCTION_NODE_TYPES (This are the languages that currently supported)
    if lang_alias not in FUNCTION_NODE_TYPES:
        print(f"Language '{lang_alias}' not supported.")
        return []

    try:
        # Generate the AST
        root_node = generate_ast(file_path)

        # Read raw bytes. Tree-sitter uses byte offsets, so we need bytes to slice the exact source code later.
        with open(file_path, 'rb') as f:
            source_bytes = f.read()
    except Exception as e:
        print(f"Error reading AST: {e}")
        return []
    
    # Helper function to convert an AST node back into readable source code string
    def get_text(node):
        if not node:
            return ""
        
        # Slice the byte array using the node's start and end byte positions, then decode to string
        return source_bytes[node.start_byte:node.end_byte].decode('utf-8')

    found_names = []
    
    # Standardize target_types to always be a list/set for uniform evaluation
    raw_target = FUNCTION_NODE_TYPES[lang_alias]
    target_types = raw_target if isinstance(raw_target, list) else [raw_target]

    # ---- LAYER 1 BOUNDARY ISOLATION ----
    # Decide WHERE in the tree we are searching (global scope vs. inside a class)
    nodes_to_check = root_node.children
    if lang_alias in CLASS_BASED_LANGUAGES:

        # If it's a class-based language, find the very first class definition
        for child in root_node.children:
            if child.type == "class_declaration":

                # Go into the body field of that class
                body = child.child_by_field_name("body")
                if body:

                    # We ONLY look at the children inside this first class, ignoring global functions
                    nodes_to_check = body.children
                break # Stop after finding the first class

    # ---- LAYER 2 EVALUATION ----
    # Iterate through the determined scope to find ALL functions
    for child in nodes_to_check:

         # Check if the current node is a function/method definition for this specific language
        if child.type in target_types:
            
            # STRATEGY 1: Standard Field Matching
            # Most languages cleanly put the function name in a "name" field
            name_node = child.child_by_field_name("name")
            
            # STRATEGY 2: C/C++ Declarator Unroller Fallback
            # C/C++ function signatures can be complex. The name is buried inside a nested "declarator" tree.
            if not name_node and lang_alias in ["cpp", "c"]:
                declarator = child.child_by_field_name("declarator")
                if declarator:
                    curr = declarator

                    # Traverse down the declarator chain until we hit the actual identifier
                    while curr:
                        if curr.type in ["identifier", "field_identifier"]:
                            name_node = curr
                            break
                        sub_name = curr.child_by_field_name("declarator")
                        if sub_name:
                            curr = sub_name
                        else:

                            # Check the direct children of the current declarator
                            for sub in curr.children:
                                if sub.type in ["identifier", "field_identifier"]:
                                    name_node = sub
                                    break
                            break

            # STRATEGY 3: Generic Fallback
            # If standard fields fail, just look for the first identifier node inside the function definition
            if not name_node:
                for sub in child.children:
                    if sub.type in ["identifier", "simple_identifier"]:
                        name_node = sub
                        break
            
            # If we successfully extracted a name, add it to the list and return it
            if name_node:
                found_names.append(get_text(name_node))

    return found_names



# find the docstring of every function using the function name (only top level function is recognised)


def find_function_docstring(file_path):
    """
    Finds top-level functions and extract their docstrings, 
    and returns a JSON string mapping function name to their docstring
    """

    # 1. Find the list of function names
    target_names = find_function_names(file_path)
    if not target_names:
        return json.dumps({})

    # Detect the language and normalize the alias string (c++ -> cpp)
    raw_alias = get_language(file_path).lower()
    lang_alias = "cpp" if raw_alias in ["cpp", "c++"] else raw_alias
    
    # Check if the language exist in the FUNCTION_NODE_TYPES (This are the languages that currently supported)
    if lang_alias not in FUNCTION_NODE_TYPES:
        print(f"Language '{lang_alias}' not supported.")
        return json.dumps({})

    try:

        # Generate the AST and read raw bytes
        root_node = generate_ast(file_path)
        with open(file_path, 'rb') as f:
            source_bytes = f.read()
    except Exception as e:
        print(f"Error reading AST: {e}")
        return json.dumps({})

    # Helper function to convert an AST node back into readable source code string
    def get_text(node):
        if not node:
            return ""
        return source_bytes[node.start_byte:node.end_byte].decode('utf-8')


    docstrings_map = {}

    # Standardize target_types to always be a list/set for uniform evaluation
    raw_target = FUNCTION_NODE_TYPES[lang_alias]
    target_types = raw_target if isinstance(raw_target, list) else [raw_target]

    # ---- LAYER 1 BOUNDARY ISOLATION ----
    # Decide WHERE in the tree we are searching (global scope vs. inside the first class)
    nodes_to_check = root_node.children
    if lang_alias in CLASS_BASED_LANGUAGES:
        for child in root_node.children:
            if child.type == "class_declaration":
                body = child.child_by_field_name("body")
                if body:
                    nodes_to_check = body.children
                break

    # ---- LAYER 2 EVALUATION & SPECIFIC SEARCH ----
    for child in nodes_to_check:
        if child.type in target_types:
            
            # STRATEGY 1: Standard Field Matching
            name_node = child.child_by_field_name("name")
            
            # C/C++ Declarator Unroller Fallback
            if not name_node and lang_alias in ["cpp", "c"]:
                declarator = child.child_by_field_name("declarator")
                if declarator:
                    curr = declarator
                    while curr:
                        if curr.type in ["identifier", "field_identifier"]:
                            name_node = curr
                            break
                        sub_name = curr.child_by_field_name("declarator")
                        if sub_name:
                            curr = sub_name
                        else:
                            for sub in curr.children:
                                if sub.type in ["identifier", "field_identifier"]:
                                    name_node = sub
                                    break
                                break

            # STRATEGY 3: Generic fallback 
            if not name_node:
                for sub in child.children:
                    if sub.type in ["identifier", "simple_identifier"]:
                        name_node = sub
                        break

            if name_node:
                func_name = get_text(name_node)
                
                # 2. Only process docstrings for functions found in the list
                if func_name in target_names:
                    docstring = ""

                    # ---- DOCSTRING EXTRACTION LAYER ----
                    if lang_alias == "python":

                        # PYTHON: Docstrings are literal string expressions inside the function body block
                        body_node = child.child_by_field_name("body")
                        if body_node and body_node.children:
                            first_stmt = body_node.children[0]

                            # Check if the first statement is an expression containing a string
                            if first_stmt.type == "expression_statement":
                                if first_stmt.children and first_stmt.children[0].type == "string":

                                    # Extract string and strip surrounding quotes and whitespace
                                    docstring = get_text(first_stmt.children[0]).strip('"\' \n\t')
                    else:

                        # OTHER LANGUAGES (C++, JS, etc.): "Docstrings" are actually comments placed ABOVE the function.
                        # We must look at previous siblings (nodes above the current function node).
                        comments = []
                        curr = child.prev_sibling
                        
                        # Walk backwards collecting any comment nodes directly attached above this function
                        while curr and curr.type in ["comment", "line_comment", "block_comment"]:
                            comments.append(get_text(curr).strip())
                            curr = curr.prev_sibling
                        
                        if comments:

                            # Reverse the list because we walked backwards (bottom-to-top), 
                            # but we want to display the comment top-to-bottom
                            comments.reverse()
                            docstring = "\n".join(comments)
                    
                    # Add the extracted docstring (or empty string if none found) to our dict
                    docstrings_map[func_name] = docstring
    
    # Return the final dictionary formatted as a pretty-printed JSON string
    return json.dumps(docstrings_map, indent=4)




# Find class definition (lua dont work since it has no class node) in AST (only top level class is recognised)

LANGUAGE_CLASS_NODE_TYPES = {
    "lua":[],
    "cpp":        ["class_specifier", "struct_specifier"],
    "python":     ["class_definition"],
    "javascript": ["class_declaration"],
    "rust":       ["struct_item", "impl_item", "enum_item"],
    "typescript": ["class_declaration"],
    "c":          ["struct_specifier"],
    "java":       ["class_declaration"],
    "go":         ["type_declaration"],
    "ruby":       ["class"],
    "kotlin":     ["class_declaration"],
    "swift":      ["class_declaration"],
    "csharp":     ["class_declaration"],
    "dart":       ["class_definition"],
    "scala":      ["class_definition"],
    "php":        ["class_declaration"],
}


def find_class_definitions(class_name, file_path):

    """
    Return the source code definition of a top level class/struct using the class name or struct name, as a string
    """

    # Detect the language and normalize the alias string (c++ -> cpp)
    raw_alias = get_language(file_path).lower()
    lang_alias = "cpp" if raw_alias in ["cpp", "c++"] else raw_alias

    # Check if the language exist in the LANGUAGE_CLASS_NODE_TYPES (This are the languages that currently supported)
    if lang_alias not in LANGUAGE_CLASS_NODE_TYPES:
        print(f"Language '{lang_alias}' not supported.")
        return None

    # Fetch the specific node types that represent classes/structs for this language
    target_types = LANGUAGE_CLASS_NODE_TYPES[lang_alias]

    # Lua early exit 
    if not target_types:
        print(f"Language '{lang_alias}' has no supported class structure.")
        return None

    try:

        # Generate the Abstract Syntax Tree and read raw bytes for source code extraction
        root_node = generate_ast(file_path)
        with open(file_path, 'rb') as f:
            source_bytes = f.read()
    except Exception as e:
        print(f"Error reading AST: {e}")
        return None

    # Helper function to convert an AST node back into readable source code string
    def get_text(node):
        if not node:
            return None
        return source_bytes[node.start_byte:node.end_byte].decode('utf-8')

    # Iterate through top-level nodes in the file
    for child in root_node.children:

        # Check if the current node matches a class/struct type for this language
        if child.type in target_types:
            
            # STRATEGY 1: Standard Field Matching
            # Most languages put the class name in a clean "name" field
            name_node = child.child_by_field_name("name")

             # STRATEGY 2: Rust `impl` Block Quirk
            # In Rust, implementation blocks (`impl ClassName`) store the target type in a "type" field, not "name"
            if not name_node and child.type == "impl_item" and lang_alias == "rust":
                name_node = child.child_by_field_name("type")
            
            # STRATEGY 3: Go / General Fallback
            if not name_node:
                if child.type == "type_declaration" and lang_alias == "go":

                    # Go wraps type definitions in a `type_declaration` -> `type_spec` -> "name" chain
                    for sub in child.children:
                        if sub.type == "type_spec":
                            name_node = sub.child_by_field_name("name")
                            break
                else:

                    # Scan immediate children for standard identifier nodes
                    # (e.g., C++ structs sometimes use "type_identifier" instead of "identifier")
                    for sub in child.children:
                        if sub.type in ["identifier", "type_identifier", "simple_identifier"]:
                            name_node = sub
                            break
            
            # If we successfully extracted a name and it matches our target, return the full class source
            if name_node and get_text(name_node) == class_name:
                return get_text(child)
    
    # If the loop finishes without returning or the class doesn't exist at the top level
    print(f"Class '{class_name}' not found at the surface level.")
    return None




# find class names as a list (also doesnt work for lua) from AST (only top level class is recognised)


def find_class_names(file_path):
    """
    Find all the top level class/struct names defined in the file and return as a list
    """

    # Detect the language and normalize the alias string (c++ -> cpp)
    raw_alias = get_language(file_path).lower()
    lang_alias = "cpp" if raw_alias in ["cpp", "c++"] else raw_alias

    # Check if the language exist in the LANGUAGE_CLASS_NODE_TYPES (This are the languages that currently supported)
    if lang_alias not in LANGUAGE_CLASS_NODE_TYPES:
        print(f"Language '{lang_alias}' not supported.")
        return []

    # Fetch the specific node types that represent classes/structs for this language
    target_types = LANGUAGE_CLASS_NODE_TYPES[lang_alias]

    # Lua early exit
    if not target_types:
        print(f"Language '{lang_alias}' has no supported class structure.")
        return []

    try:

        # Generate the Abstract Syntax Tree and read raw bytes for source code extraction
        root_node = generate_ast(file_path)
        with open(file_path, 'rb') as f:
            source_bytes = f.read()
    except Exception as e:
        print(f"Error reading file/AST: {e}")
        return []

    # Helper function to convert an AST node back into readable source code string
    def get_text(node):
        if not node:
            return None
        return source_bytes[node.start_byte:node.end_byte].decode('utf-8')

    found_names = []

    # Iterate through top-level nodes in the file
    for child in root_node.children:

        # Check if the current node matches a class/struct type for this language
        if child.type in target_types:

            # STRATEGY 1: Standard Field Matching
            # Most languages put the class name in a clean "name" field
            name_node = child.child_by_field_name("name")

            # STRATEGY 2: Rust `impl` Block Quirk
            # In Rust, implementation blocks store the target type in a "type" field
            if not name_node and child.type == "impl_item" and lang_alias == "rust":
                name_node = child.child_by_field_name("type")

            # STRATEGY 3: Go / Generic Fallback
            if not name_node:
                if child.type == "type_declaration" and lang_alias == "go":
                    for sub in child.children:
                        if sub.type == "type_spec":
                            name_node = sub.child_by_field_name("name")
                            break
                else:

                    # Scan immediate children for standard identifier nodes.
                    # (Added "constant" and "name" to catch edge-cases)
                    for sub in child.children:
                        if sub.type in ["identifier", "type_identifier", "simple_identifier", "constant", "name"]:
                            name_node = sub
                            break
            
            # If we successfully extracted a name, add it to our collection list
            if name_node:
                found_names.append(get_text(name_node))

    # Return the list
    return found_names



# Find the docstring of every class using the class name (only top level class is recognised) (Also doesnt work for lua)
def find_class_docstring(file_path):
    """
    Finds top-level classes/structs and extracts their docstrings, and returns a JSON string mapping class/struct name to their docstring.
    """

    # 1. Find class names
    target_names = find_class_names(file_path)
    if not target_names:
        return json.dumps({})

    # Detect the language and normalize the alias string (c++ -> cpp)
    raw_alias = get_language(file_path).lower()
    lang_alias = "cpp" if raw_alias in ["cpp", "c++"] else raw_alias

    # Check if we have a known mapping of AST node types for classes in this language
    if lang_alias not in LANGUAGE_CLASS_NODE_TYPES:
        print(f"Language '{lang_alias}' not supported.")
        return json.dumps({})

    # Fetch the specific node types that represent classes/structs for this language
    target_types = LANGUAGE_CLASS_NODE_TYPES[lang_alias]

    # Lua early exit
    if not target_types:
        print(f"Language '{lang_alias}' has no supported class structure.")
        return json.dumps({})

    try:

        # Generate the Abstract Syntax Tree and read raw bytes for source code extraction
        root_node = generate_ast(file_path)
        with open(file_path, 'rb') as f:
            source_bytes = f.read()
    except Exception as e:
        print(f"Error reading file/AST: {e}")
        return json.dumps({})

    # Helper function to convert an AST node back into readable source code string
    def get_text(node):
        if not node:
            return ""
        return source_bytes[node.start_byte:node.end_byte].decode('utf-8')

    docstrings_map = {}

    # ---- EVALUATION & SPECIFIC SEARCH ----
    # Iterate through top-level nodes in the file
    for child in root_node.children:
        if child.type in target_types:

            # --- NAME RESOLUTION (Replicating the exact fallback logic from find_class_names) ---
            
            # STRATEGY 1: Standard Field Matching
            name_node = child.child_by_field_name("name")

            # STRATEGY 2: Rust `impl` Block Quirk
            if not name_node and child.type == "impl_item" and lang_alias == "rust":
                name_node = child.child_by_field_name("type")

            # STRATEGY 3: Go / Generic Fallback   
            if not name_node:
                if child.type == "type_declaration" and lang_alias == "go":

                    # Go wraps type definitions in a `type_declaration` -> `type_spec` -> "name" chain
                    for sub in child.children:
                        if sub.type == "type_spec":
                            name_node = sub.child_by_field_name("name")
                            break
                else:

                    # Scan immediate children for identifier nodes
                    for sub in child.children:
                        if sub.type in ["identifier", "type_identifier", "simple_identifier", "constant", "name"]:
                            name_node = sub
                            break

            if name_node:
                class_name = get_text(name_node)
                
                # Only process docstrings for classes we identified in Step 1
                if class_name in target_names:
                    docstring = ""

                    # ---- DOCSTRING EXTRACTION LAYER ----
                    if lang_alias == "python":

                        # PYTHON: Class docstrings are literal string expressions inside the class body block
                        body_node = child.child_by_field_name("body")
                        if body_node and body_node.children:
                            first_stmt = body_node.children[0]

                            # Check if the first statement is an expression containing a string
                            if first_stmt.type == "expression_statement":
                                if first_stmt.children and first_stmt.children[0].type == "string":

                                    # Extract string and strip surrounding quotes and whitespace
                                    docstring = get_text(first_stmt.children[0]).strip('"\' \n\t')
                    else:
                        # Other languages place documentation comments directly above the class/struct declaration

                        comments = []
                        curr = child.prev_sibling
                        
                        # Walk backwards collecting any comment nodes directly attached above this class
                        while curr and curr.type in ["comment", "line_comment", "block_comment"]:
                            comments.append(get_text(curr).strip())
                            curr = curr.prev_sibling
                        
                        if comments:
                            
                            # Reverse the list because we walked backwards (bottom-to-top), 
                            # but we want to display the comment top-to-bottom
                            comments.reverse()  
                            docstring = "\n".join(comments)

                    # Safety Check: If a language allows multiple definitions blocks (like Rust `impl` blocks), 
                    # If the second `impl` block has NO comment above it, we don't want an empty string 
                    # to overwrite the valid docstring we already extracted from the first `impl` block.
                    if docstring or class_name not in docstrings_map:
                        docstrings_map[class_name] = docstring
    
    # Return the final dictionary formatted as a pretty-printed JSON string
    return json.dumps(docstrings_map, indent=4)



######################### Github/Gitlab API Usage #####################################





PLATFORM_CONFIG = {
    "github": {
        "search_url": "https://api.github.com/search/repositories",
        "headers": {
            "Accept":               "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        "auth_style":     "bearer",
        "params_builder": lambda query, page, per_page: {
            "q":        query,
            "page":     page,
            "per_page": per_page,
        },
        "results_key":    "items",
        "name_key":       "full_name",
    },

    "gitlab": {
        "search_url": "https://gitlab.com/api/v4/projects",
        "headers": {
            "Content-Type": "application/json",
        },
        "auth_style":     "private_token",
        "params_builder": lambda query, page, per_page: {
            "search":   query,
            "page":     page,
            "per_page": per_page,
        },
        "results_key":    None,
        "name_key":       "path_with_namespace",
    },
}




REPO_STRUCTURE_CONFIG = {
    "github": {
        "contents_url": "https://api.github.com/repos/{owner}/{repo}/contents/{path}",
        "headers": {
            "Accept":               "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        "auth_style": "bearer",
        "file_key":   "name",
        "type_key":   "type",
        "file_type":  "file",
        "dir_type":   "dir",
    },

    "gitlab": {
        "project_url":  "https://gitlab.com/api/v4/projects/{owner}%2F{repo}",
        "contents_url": "https://gitlab.com/api/v4/projects/{project_id}/repository/tree",
        "headers": {
            "Content-Type": "application/json",
        },
        "auth_style": "private_token",
        "file_key":   "name",
        "type_key":   "type",
        "file_type":  "blob",
        "dir_type":   "tree",
    },
}




def attach_auth(headers, auth_style, token):
    """Attach auth token to headers based on platform."""

    # Defensive check: If the token is missing, empty, or the literal string "None" 
    # (which can happen if environment variables aren't set correctly), just return 
    # the headers unchanged to prevent sending invalid "None" strings to the API.
    if not token or token in ("", "None", "null"):
        return headers  # no auth — return headers unchanged
    
    # Standard OAuth2 / GitHub style authentication
    if auth_style == "bearer":
        headers["Authorization"] = f"Bearer {token}"

    # GitLab specific authentication style    
    elif auth_style == "private_token":
        headers["PRIVATE-TOKEN"] = token
    return headers




def get_api_token(provider: str) -> str:
    """
    Dynamically reloads the env file from disk and fetches the requested 
    provider's token string, catching mid-session changes instantly.
    """
    
    # Normalize the provider string to guarantee it matches the uppercase format used in the env key
    provider = provider.upper().strip()
    
    # We reload the .env file on EVERY call with override=True.
    # This ensures that if a user updates a token in the TUI settings modal mid-session, 
    # the very next API call will pick up the new token without needing to restart the app.
    load_dotenv(dotenv_path=ENV_FILE,override=True)
    
    # Construct the exact key name (e.g., "GITHUB_TOKEN") and fetch it. 
    # Returns an empty string if the key doesn't exist, preventing NoneType errors later.
    env_key = f"{provider}_TOKEN"
    
    return os.environ.get(env_key, "")



# ─────────────────────────────────────────────
# SEARCH REPOS
# ─────────────────────────────────────────────

@tool
def search_repos(
    keywords: str, 
    platform: str = "github",  
    page: int = 1, 
    per_page: int = 10
) -> str:
    
    """
    Search for repository names across Github and Gitlab using a string of python list of keywords.
    create a string of list of keywords by yourself if it not given by the user.

    Parameters:

        keywords: A String of python list of keywords, (e.g. "["tree-sitter", "python"]"). Cannot be empty.

        platform: The target platform. Must be either "github" or "gitlab".

    Returns:

        A JSON string containing a list of matched repository names in 'owner/repository' format.
    """
    
    # Dynamically fetch the latest token from the env file for this particular platform
    token = get_api_token(platform)

    # Safely convert the string representation of a list into an actual Python list object. We use literal_eval instead of eval() for security.
    try:
        keywords = ast.literal_eval(keywords)
    except Exception:
        return "Error: keywords argument must be a valid string representation of a list."
    
    # Validate that the parsed result is actually a list and isn't empty
    if not keywords or not isinstance(keywords, list):
        return "Error: keywords argument must be a non-empty string of list."
    
    # Normalize platform string and verify it exists in the configuration dict
    platform_key = platform.lower().strip()
    if platform_key not in PLATFORM_CONFIG:
        return f"Error: platform '{platform}' not supported. Choose from: {list(PLATFORM_CONFIG.keys())}"

    

    # 2. Build Request Configuration

    # Search APIs expect a single string query, so we join the list of keywords with spaces
    query = " ".join(keywords)

    # Fetch platform-specific settings (URLs, default headers etc.) from the config map
    config = PLATFORM_CONFIG[platform_key] 
    
    # Attach the appropriate authentication header (Bearer for GitHub, PRIVATE-TOKEN for GitLab)
    headers = attach_auth(dict(config["headers"]), config["auth_style"], token)

    # Add a global User-Agent here to make sure GitHub doesn't reject search request

    headers["User-Agent"] = "TextualMyloAgent/1.0"
    params = config["params_builder"](query, page, per_page)

    # 3. Execute Single Request
    try:
        response = requests.get(
            config["search_url"],
            headers=headers,
            params=params,
            timeout=10, # fail fast if the network is hanging
        )
        response.raise_for_status() # Automatically throws an HTTPError for 4xx/5xx responses
        data = response.json() 

        # 4. Extract and Parse Data Keys Safely
        # Dynamically locate the list of results in the JSON response (e.g., "items" for GitHub, "data" for GitLab)
        results_key = config.get("results_key")
        items = data.get(results_key, []) if results_key else (data if isinstance(data, list) else [])
        
        # Dynamically find the key that holds the repo name (e.g., "full_name" for GitHub)
        name_key = config.get("name_key", "full_name")
        
        # Build a clean list of just the repository names
        results = [item[name_key] for item in items if name_key in item]

        if not results:
            return f"No active repositories matching keywords {keywords} were found on {platform_key}."
        
        # Return the final list as a JSON string for the LLM to easily parse
        return json.dumps(results)


    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"The {platform_key} platform API is currently unreachable. Check your network.") from e

    except requests.exceptions.HTTPError as e:
        status = e.response.status_code
        
        # Handle specific, common API failures with helpful error messages
        if status in [401, 403]:
            raise PermissionError(
                f"[{platform_key}] Authentication Failed (Status {status}): "
                f"The token is missing, invalid, expired, or lacks the required scopes."
            ) from e
        
        if status == 429:
            raise RuntimeError(f"Rate limit exceeded on [{platform_key}] (Status 429).") from e

        # Catch-all for other HTTP errors (e.g., 500 Internal Server Error)    
        raise RuntimeError(f"HTTP error occurred on [{platform_key}]: Status {status} - {e.response.text}") from e

    except Exception as e:

        # Catch unexpected issues like JSON decoding failures or missing keys
        raise RuntimeError(f"Unexpected parsing failure on [{platform_key}]: {str(e)}") from e



# ─────────────────────────────────────────────
# FIND REPO STRUCTURE 
# ─────────────────────────────────────────────


REPO_STRUCTURE_CONFIG = {
    "github": {
        "tree_url":  "https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1",
        "headers": {
            "Accept":               "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent":           "TextualMyloAgent/1.0"
        },
        "auth_style":    "bearer",
        "tree_key":      "tree",
        "path_key":      "path",
        "type_key":      "type",
        "file_type":     "blob",
        "truncated_key": "truncated",
    },
    "gitlab": {
        "tree_url":    "https://gitlab.com/api/v4/projects/{owner}%2F{repo}/repository/tree?recursive=true&per_page=100&page={page}",
        "headers": {
            "Content-Type": "application/json",
            "User-Agent":   "TextualMyloAgent/1.0"
        },
        "auth_style":    "private_token",
        "tree_key":      None,
        "path_key":      "path",
        "type_key":      "type",
        "file_type":     "blob",
        "truncated_key": None,
    },
}


@tool
def find_repo_structure(repo_full_name: str, platform: str = "github") -> str:
    """
    Returns full repo structure in a json string, where the key is the folder name and the value is the list of filenames in that folder (the outermost folder is named as root (where README.md is present)). 
    Use this tool when the user wanted to see the full file structure of the repo

    Nested folder are given like this parentdir/childdir (where parentdir is the outer folder and the childdir is the inner folder)

    Parameters:

        repo_full_name : "owner/repo" repo name string

        platform       : "github" or "gitlab"

    Output: a json string

        (e.g. {"root": ["README.md","logo.png"], "src": ["main.py","test.py"], "src/utils": ["helper.py"]} )
    """

    # Fetch the api token dynamically
    token = get_api_token(platform)

    # Basic input validation: ensure the repo name contains the required slash separator
    if "/" not in repo_full_name:
        return "Error: repo_full_name must be in 'owner/repo' format."

    # Normalize platform string and verify it exists in our configuration
    platform = platform.lower().strip()
    if platform not in REPO_STRUCTURE_CONFIG:
        return f"Error: platform '{platform}' not supported."

    # Split "owner/repo" into two separate variables for URL formatting
    owner, repo = repo_full_name.split("/", 1)

    # Fetch platform-specific settings (URLs, header templates etc.)
    config      = REPO_STRUCTURE_CONFIG[platform]
    headers     = attach_auth(dict(config["headers"]), config["auth_style"], token)

    
    # Extract the dynamic JSON keys needed to parse the API response 
    path_key  = config["path_key"]
    type_key  = config["type_key"]
    file_type = config["file_type"]


    structure = {}

    # --- HELPER FUNCTION: FLATTEN THE TREE ---
    # APIs return a flat list of full paths. This function groups them into folder/file format.
    def build_structure(items):
        for item in items:

            # Skip directories/folders, we only care about actual files
            if item.get(type_key) != file_type:
                continue
            full_path = item.get(path_key, "")
            if "/" in full_path:

                # If it's a nested file, Split from the right once to get the immediate parent folder and the filename
                folder   = full_path.rsplit("/", 1)[0]
                filename = full_path.rsplit("/", 1)[1]
            else:

                # If it's a root-level file, Assign it to our special "root" key
                folder   = "root"
                filename = full_path

            # Initialize the folder list if it doesn't exist yet, then append the file    
            if folder not in structure:
                structure[folder] = []
            structure[folder].append(filename)

    try:
        
        # --- GITHUB API FLOW ---
        if platform == "github":

            # GitHub provides a recursive tree API that gets the whole repo in one request
            tree_url = config["tree_url"].format(owner=owner, repo=repo)
            resp     = requests.get(tree_url, headers=headers, timeout=30)

            # GitHub often throws 403 specifically for rate limits on unauthenticated requests
            if resp.status_code == 403:
                return "Error: GitHub rate limit reached. Pass a token to increase your limit."
            if resp.status_code == 404:
                return f"Error: Repo '{repo_full_name}' not found on GitHub."

            resp.raise_for_status()
            data  = resp.json()
            items = data.get(config["tree_key"], [])

            # GitHub API warns you if the repo is too large and the tree was truncated.
            # We capture this as a special key in our JSON output so the LLM knows data might be missing.
            if data.get(config["truncated_key"]):
                structure["_warning"] = ["Repo is very large, results may be truncated."]

            build_structure(items)

        # --- GITLAB API FLOW ---
        elif platform == "gitlab":
            page = 1

            # GitLab's tree API is paginated, so we must loop until we get an empty page
            while True:
                # Format using owner and repo directly, no project_id lookup needed
                tree_url = config["tree_url"].format(owner=owner, repo=repo, page=page)
                resp     = requests.get(tree_url, headers=headers, timeout=30)
                
                # Only check for 404 on the first page. (Empty pages return 200 with an empty list [])
                if resp.status_code == 404 and page == 1:
                    return f"Error: Repo '{repo_full_name}' not found on GitLab."
                
                resp.raise_for_status()
                page_items = resp.json()

                # An empty list signals we've reached the end of the repository files
                if not page_items:
                    break
                
                # Process this batch of files and move to the next page
                build_structure(page_items)
                page += 1
        
        # Final check: if the dictionary is still empty, the repo had no files
        if not structure:
            return "No files found in the repository."
        
        # Return the grouped dictionary as a formatted JSON string
        return json.dumps(structure, indent=4)

    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"Network Error: Could not reach {platform}. Check your network") from e

    except requests.exceptions.HTTPError as e:
        status = e.response.status_code
        if status in [401, 403]:
            raise PermissionError(
                f"[{platform}] Authentication Failed (Status {status}): Token is invalid, expired, or missing proper scopes."
            ) from e
        
        if status == 429:
            raise RuntimeError(f"Rate limit exceeded on [{platform}] (Status 429).") from e

        raise RuntimeError(f"HTTP error [{platform}]: {status} - {e.response.text}") from e

    except Exception as e:
        raise RuntimeError(f"Unexpected error: {str(e)}") from e


  
# ─────────────────────────────────────────────
# FETCH FILE 
# ─────────────────────────────────────────────

FILE_FETCH_CONFIG = {
    "github": {
        "file_url":   "https://api.github.com/repos/{owner}/{repo}/contents/{path}",
        "headers": {
            "Accept":               "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent":           "TextualMyloAgent/1.0" 
        },
        "auth_style":    "bearer",
        "content_style": "base64",
    },

    "gitlab": {
        "file_url":    "https://gitlab.com/api/v4/projects/{owner}%2F{repo}/repository/files/{encoded_path}/raw",
        "headers": {
            "Content-Type": "application/json",
            "User-Agent":   "TextualMyloAgent/1.0"
        },
        "auth_style":    "private_token",
        "content_style": "raw",
    },
}


@tool
def fetch_file(file_path: str, repo_full_name: str, platform: str = "github") -> str:
    """
    Fetches a file as a string using its full path in the repo. Use it when user wanted to see the file content or fetch a file.

    Parameters:

        file_path      : Full path to the file in the repo (folder/file format) (e.g. "src/utils/helper.py")

                         For files in root folder, the path is just the filename (e.g. "README.md")

        repo_full_name : "owner/repo" name string

        platform       : "github" or "gitlab"

    Output:

        Output file content as a string

    """
    
    # Fetch the api token key dynamically
    token = get_api_token(platform)

    
    # Basic input validation: ensure the repo name contains the required slash separator
    if "/" not in repo_full_name:
        return "Error: repo_full_name must be in 'owner/repo' format."
    
    # Normalize platform string and verify it exists in our configuration
    platform = platform.lower().strip()
    if platform not in FILE_FETCH_CONFIG:
        return f"Error: platform '{platform}' not supported."
    
    # Split "owner/repo" into two separate variables for URL formatting
    owner, repo = repo_full_name.split("/", 1)

    # Fetch platform-specific settings (URLs, header templates etc.)
    config      = FILE_FETCH_CONFIG[platform]
    headers     = attach_auth(dict(config["headers"]), config["auth_style"], token)
    
    

    try:

        # --- GITHUB API FLOW ---
        if platform == "github":
            url      = config["file_url"].format(owner=owner, repo=repo, path=file_path)
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data     = response.json()

            # CRITICAL: GitHub's API returns file contents as a Base64 encoded string under the "content" key
            encoded  = data.get("content", "")
            if not encoded:
                return f"Error: file '{file_path}' exists but has no content."
            
            # Decode the Base64 string back into readable UTF-8 text
            return base64.b64decode(encoded).decode("utf-8")

        # --- GITLAB API FLOW ---
        elif platform == "gitlab":

            # GitLab URLs require the file path to be URL-encoded (e.g., turning "src/utils/file.py" 
            # into "src%2Futils%2Ffile.py"). If we don't do this, the API will break on the slashes.
            # safe="" ensures that slashes are encoded too.
            encoded_path = requests.utils.quote(file_path, safe="")

            # Pass owner, repo, and the safely encoded path into the formatted string
            url          = config["file_url"].format(
                owner=owner,
                repo=repo,
                encoded_path=encoded_path
            )
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            # Unlike GitHub, GitLab's raw file endpoint directly returns the plain text content
            content  = response.text
            if not content or not content.strip():
                return f"Error: file '{file_path}' exists but has no content."
            return content

    except requests.exceptions.HTTPError as e:
        status = e.response.status_code
        
        # Handle specific HTTP status codes with helpful, context-aware error messages
        if status == 404:
            raise FileNotFoundError(f"Error: File '{file_path}' not found in repository '{repo_full_name}'.") from e
            
        if status in [401, 403]:
            raise PermissionError(
                f"[{platform}] Authentication Failed (Status {status}): Rate limit reached, "
                f"token expired, or access denied for file '{file_path}'."
            ) from e
            
        if status == 429:
            raise RuntimeError(f"Rate limit exceeded on [{platform}] (Status 429).") from e
            
        # Catch-all for other unhandled HTTP errors    
        raise RuntimeError(f"HTTP error [{platform}]: {status} - {e.response.text}") from e

    except requests.exceptions.RequestException as e:

        # Catches broader network issues like DNS failures, timeouts, or connection resets
        raise RuntimeError(f"Request error [{platform}]: {str(e)}") from e

    except Exception as e:

        # Absolute fallback for unexpected issues (e.g., Base64 decoding errors due to corrupted API response)
        raise RuntimeError(f"Unexpected error fetching '{file_path}': {str(e)}") from e



# ─────────────────────────────────────────────
# WITH TEMP FILE
# ─────────────────────────────────────────────

@tool
def with_temp_file(
    file_path:      str,
    repo_full_name: str,
    platform:       str,
    func:           str,
    extra_arg:      str = "",
) -> str:
    """
    Use it to Analyses a fetched file from github/gitlab deeper.

    
Parameters:

    file_path      : full path to file in repo (e.g. "src/main.py")
    repo_full_name : full name of the repo "owner/repo" format (e.g. "shelsoloa/Peachy")
    platform       : "github" or "gitlab"
    func           : name of the AST extraction functions (see options below)
    extra_arg      : extrac arguments that are required by some funcs (see below)

func options:
    "find_function_names"       -> get all function names as a list. extra_arg: not needed
    "find_function_definitions" -> get source code definition of a function using the function name (find it using find_function_names if not given). (Can be used to find parameters, their type and return type of a function by analysing it.) extra_arg: function_name
    "find_function_docstring"   -> get the docstring of each function and return as a json string (where key is the function name and the value is the docstring (e.g. {"func1":"doc1","func2:"doc2"})). (Can be used to understand more about a function by analysing its docstring.) extra_arg: not needed
    "find_class_names"          -> get all class names in a file as a list.    extra_arg: not needed
    "find_class_definitions"    -> get source code definitions of a class using class name (find it using find_class_names if not given). (can be used to answer any query from user related to a particular class by analysing it.) extra_arg: class_name
    "find_class_docstring"      -> get the docstring of each class and return as a json string (where key is the class name and the value is the docstring (e.g. {"class1":"doc1","class2:"doc2"})). (Can be used to understand more about a class by analysing its docstring.) extra_arg: not needed
    """


    # Create a dispatch dictionary mapping the string names to the actual Python AST functions
    func_map = {
        "find_function_names":       find_function_names,
        "find_function_definitions": find_function_definitions,
        "find_function_docstring":   find_function_docstring,
        "find_class_names":          find_class_names,
        "find_class_definitions":    find_class_definitions,
        "find_class_docstring":      find_class_docstring
    }
    
    # Validate that the requested function name actually exists in our map
    if func not in func_map:
        return f"Error: unknown function '{func}'. Valid options are: {list(func_map.keys())}"

    # Get the actual callable function object
    actual_func = func_map[func]

    # Extract the original filename (e.g., "helper.py") and its extension (e.g., ".py")
    bare_name   = os.path.basename(file_path)
    ext         = os.path.splitext(bare_name)[1]

    # Create a prefix for the temp file (e.g., "helper_"). 
    # This ensures the temp file looks like "helper_12345.py", which helps Pygments/Tree-sitter 
    # correctly identify the language based on the extension.
    prefix      = os.path.splitext(bare_name)[0] + "_"

    # Fetch the file content from the repository.
    # Note: We use `.func` here because `fetch_file` is wrapped as a LangChain @tool. 
    # Accessing `.func` bypasses the tool schema validation and calls the raw Python function directly.
    file_content = fetch_file.func(
        file_path,
        repo_full_name,
        platform,
    )
    
    # Verify that the file actually contained data
    if not file_content:
        return f"Error: could not fetch '{file_path}' from '{repo_full_name}' on {platform}."

    # Create a physical temporary file on disk.
    # delete=False is CRITICAL: Tree-sitter requires a real file path to parse. 
    # We handle manual deletion in the `finally` block.
    with tempfile.NamedTemporaryFile(
        mode='w', suffix=ext, prefix=prefix,
        delete=False, encoding='utf-8'
    ) as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name # Save the path so we can pass it to the AST functions

    try:

        # Funtions that dont requires extra args other than filepath
        no_arg_funcs = {"find_function_names","find_function_docstring", "find_class_names", "find_class_docstring"}

        if func in no_arg_funcs:

            # Execute the function with the filepath = temp file path
            result = actual_func(tmp_path)
        else:
            # Execute functions that requires extra arg
            if not extra_arg or extra_arg.strip() == "":
                return f"Error: '{func}' requires extra_arg (function_name or class_name), but not given. Call find_function_names or find_class_names first to get the extra arg."
            result = actual_func(extra_arg, tmp_path)

        # --- RESULT NORMALIZATION ---
        # AST functions return different types (lists, dicts, strings, or None).
        # We must standardize the output so the LLM doesn't break trying to parse inconsistent types.
        if result is None:
            return f"No results found for '{func}' on '{file_path}'."

        # If the function returned a list or dict, convert it to a JSON string for safe LLM consumption
        result_str = json.dumps(result) if isinstance(result, (list, dict)) else str(result)

        # Catch edge cases where the result is technically valid JSON but completely empty
        if not result_str or result_str.strip() in ("", "[]", "{}", "None"):
            return f"'{func}' returned no results for '{file_path}'. The file may not contain the requested function or class."

        return result_str

    except Exception as e:

        # Catch any Tree-sitter parsing crashes or file reading errors and return them as safe strings
        return f"Error while running '{func}' on '{file_path}': {str(e)}"

    finally:

        # CRITICAL CLEANUP: Guarantee the temporary file is deleted from the user's disk
        # regardless of whether the analysis succeeded or threw an exception.
        os.unlink(tmp_path)



@tool
def save_string_to_file(content: str, filepath: str) -> None:
    """
    Saves the string of a file at a specified path.
    Automatically creates missing parent directories if they don't exist.

    Parameters:-
        content: the string that needs to be saved
        filepath: path to which the file is to be saved
    """
    try:
        path = Path(filepath)
        
        # Ensure the directory structure exists before writing
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write the text content safely
        path.write_text(content, encoding="utf-8")
        print(f"File successfully saved to: {path}")
        
    except Exception as e:
        print(f"Failed to save file due to error: {e}")
        raise e



tools =[

search_repos,
with_temp_file,
find_repo_structure,
fetch_file,
save_string_to_file   

]


