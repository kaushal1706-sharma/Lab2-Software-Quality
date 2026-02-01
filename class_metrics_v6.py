import os
import ast
import subprocess
import csv
from collections import defaultdict
import sys
import networkx as nx

# The script computes the following metrics:
# 1. LOC (lines in class body)
# 2. Number of methods
# 3. Commit count (changes): Class Change Frequency (Number of commits where the class was modified)
# More precisely:
# It uses git log -L to track the history of lines that belong to a specific class definition.
# Each time Git detects a change in the lines belonging to that class (e.g., a method added, modified, or removed),
# it counts as one "change".
# The output.count("commit") counts how many commits affected that class's code block.
# 4. Lines added/deleted
# 5. Number of unique authors
# 6. File path where the class is saved
# 7. Lack of Cohesion in Methods (LCOM): Measures internal relatedness of methods, Measures the number of method pairs that do not share instance variables
# defined as:
# LCOM = number of method pairs that do not share fields  - number of method pairs that do share fields
# If result < 0, set LCOM = 0
# LCOM value:
# 0 = perfect cohesion (best)
# Higher numbers = more separation between methods, lower cohesion (bad).
# 8.Coupling Between Objects (CBO): Counts the number of distinct external classes a class uses (through attributes, method calls, 
# inheritance).
# High CBO = more interdependence = harder to modify/test 
# 9. 7.	Number of Lines Changed per Commit (NLC) = Total lines changed (added + deleted) / Number of commits that changed the class
# It shows the average impact of each change:
# High NLC = each commit makes large changes → possibly unstable or poorly isolated class
# Low NLC = smaller, incremental changes → often more maintainable
# 10. Tight Class Cohesion (TCC): is a software metric used to measure how well the methods of a class work together,
#  i.e., how tightly connected the internal methods of a class are via shared fields (attributes).
#  TCC = (Number of directly connected method pairs) / (Total possible method pairs)
#  A method pair is directly connected if both methods access at least one common instance variable (e.g., self.name).
#  Total possible method pairs = n(n - 1)/2, where n = number of methods.
#  TCC Value	Interpretation
#  = 1.0	    All methods are tightly connected
#   > 0.5	    High cohesion
#   0.3–0.5 	Moderate cohesion
#   < 0.3	    Low cohesion (candidate for refactor
# 11. Fan in: the number of modules or classes that call/use a given class or method.
# 12 Fan out: the number of other modules, classes, or methods that a given class or method calls or depends on.
#################################

def get_py_files(repo_path):
    py_files = []
    for root, _, files in os.walk(repo_path):
        for file in files:
            if file.endswith('.py'):
                py_files.append(os.path.join(root, file))
    return py_files

def extract_class_metrics(file_path, repo_path):
    with open(file_path, "r", encoding="utf-8") as f:
        code = f.read()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    class_metrics = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = [n for n in node.body if isinstance(n, ast.FunctionDef)]
            attributes = defaultdict(set)

            for method in methods:
                method_name = method.name
                for subnode in ast.walk(method):
                    if isinstance(subnode, ast.Attribute):
                        if isinstance(subnode.value, ast.Name) and subnode.value.id == "self":
                            attributes[method_name].add(subnode.attr)

            # Calculate LCOM
            method_names = list(attributes.keys())
            no_shared = 0
            shared = 0
            for i in range(len(method_names)):
                for j in range(i + 1, len(method_names)):
                    set_i = attributes[method_names[i]]
                    set_j = attributes[method_names[j]]
                    if set_i & set_j:
                        shared += 1
                    else:
                        no_shared += 1
            lcom = no_shared - shared
            if lcom < 0:
                lcom = 0

            # Calculate TCC
            connected = 0
            total = 0
            for i in range(len(method_names)):
                for j in range(i + 1, len(method_names)):
                    set_i = attributes[method_names[i]]
                    set_j = attributes[method_names[j]]
                    total += 1
                    if set_i & set_j:
                        connected += 1
            tcc = round(connected / total, 3) if total > 0 else 1.0

            # Calculate CBO (coupling): count distinct class names used
            imported_classes = set()
            for subnode in ast.walk(node):
                if isinstance(subnode, ast.Name) and subnode.id != node.name:
                    imported_classes.add(subnode.id)

            class_metrics.append({
                "filename": os.path.relpath(file_path, repo_path),
                "class": node.name,
                "loc": len(node.body),
                "methods": len(methods),
                "lcom": lcom,
                "tcc": tcc,
                "cbo": len(imported_classes),
            })
    return class_metrics

def get_class_git_stats(file_path, class_name, repo_path):
    rel_path = os.path.relpath(file_path, repo_path)
    try:
        log_output = subprocess.check_output(
            ["git", "log", "-L", f":class {class_name}:{rel_path}"],
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=repo_path
        )
    except subprocess.CalledProcessError:
        return {
            "changes": 0,
            "lines_added": 0,
            "lines_deleted": 0,
            "authors": set(),
            "nlc" : 0
        }

    changes = log_output.count("commit")
    lines_added = 0
    lines_deleted = 0
    authors = set()

    for line in log_output.splitlines():
        if line.startswith("Author:"):
            authors.add(line.split(":", 1)[1].strip())
        elif line.startswith("+") and not line.startswith("+++"):
            lines_added += 1
        elif line.startswith("-") and not line.startswith("---"):
            lines_deleted += 1
    total_lines_changed = lines_added + lines_deleted
    nlc = round(total_lines_changed / changes, 2) if changes > 0 else 0

    return {
        "changes": changes,
        "lines_added": lines_added,
        "lines_deleted": lines_deleted,
        "authors": authors,
        "nlc": nlc
    }
   

def compute_fan_in_out(dot_file="classes.dot"):
    print("computing fan-in and fan-out using dot file: "+dot_file)
    G = nx.drawing.nx_pydot.read_dot(dot_file)
    fan_data = {}
    for node in G.nodes:
        clean_name = node.strip('"').split('.')[-1]
        fan_in = len(list(G.predecessors(node)))
        fan_out = len(list(G.successors(node)))
        fan_data[clean_name] = {"fan_in": fan_in, "fan_out": fan_out}
    return fan_data

def export_to_csv(results, fan_data, output_file):
    #fieldnames = ["class", "filename", "loc", "methods", "lcom", "tcc", "cbo", "changes", "lines_added", "lines_deleted", "authors", "fan_in", "fan_out"]
    fieldnames = ["class", "filename", "loc", "methods", "lcom", "tcc", "cbo", "changes", "lines_added", "lines_deleted", "nlc", "authors", "fan_in", "fan_out"]

    with open(output_file, mode="w", newline='', encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            fan_in = fan_data.get(r["class"], {}).get("fan_in", 0)
            fan_out = fan_data.get(r["class"], {}).get("fan_out", 0)
            writer.writerow({
                "class": r["class"],
                "filename": r["filename"],
                "loc": r["loc"],
                "methods": r["methods"],
                "lcom": r["lcom"],
                "tcc": r["tcc"],
                "cbo": r["cbo"],
                "changes": r["changes"],
                "lines_added": r["lines_added"],
                "lines_deleted": r["lines_deleted"],
                "nlc": r["nlc"],
                "authors": len(r["authors"]),
                "fan_in": fan_in,
                "fan_out": fan_out
            })
    print(f"\n📁 CSV export complete: {output_file}")

def run_pyreverse(target_path, project_name):
    print(f"\n♻️ Running pyreverse on: {target_path}")
    subprocess.run(["pyreverse", "-o", "dot", "-p", project_name, target_path], check=True)

def main():
    if len(sys.argv) > 1:
        repo_path = sys.argv[1]
        if len(sys.argv) > 2:
            OUTPUT_FILE = sys.argv[2]
    else:
        print("Usage: python class_metrics_v5.py repo_path output_filename(with no extension)")
        sys.exit()

    results = []
    files = get_py_files(repo_path)
    print("Computing the first set of class metrics...")
    for file_path in files:
        classes = extract_class_metrics(file_path, repo_path)
        for cls in classes:
            stats = get_class_git_stats(file_path, cls["class"], repo_path)
            cls.update(stats)
            results.append(cls)

    run_pyreverse(repo_path, repo_path)
    project_name = repo_path.replace("/", "_")
    dot_file_path = f"classes_{project_name}.dot"
    fan_data = compute_fan_in_out(dot_file_path)

    #print(f"{'Class':20} {'LOC':>5} {'Meth':>5} {'LCOM':>5} {'TCC':>5} {'CBO':>5} {'Chg':>5} {'+Lines':>7} {'-Lines':>7} {'Auth':>5} {'In':>4} {'Out':>4} Filename")
    print(f"{'Class':20} {'LOC':>5} {'Meth':>5} {'LCOM':>5} {'TCC':>5} {'CBO':>5} {'Chg':>5} {'+Lines':>7} {'-Lines':>7} {'NLC':>6} {'Auth':>5} {'In':>4} {'Out':>4} Filename")


    print("-" * 150)
    for r in results:
        fan_in = fan_data.get(r["class"], {}).get("fan_in", 0)
        fan_out = fan_data.get(r["class"], {}).get("fan_out", 0)
        print(f"{r['class']:20} {r['loc']:>5} {r['methods']:>5} {r['lcom']:>5} {r['tcc']:>5.2f} {r['cbo']:>5} {r['changes']:>5} {r['lines_added']:>7} {r['lines_deleted']:>7} {r['nlc']:>6.2f} {len(r['authors']):>5} {fan_in:>4} {fan_out:>4} {r['filename']}")


    export_to_csv(results, fan_data, OUTPUT_FILE + ".csv")

if __name__ == "__main__":
    main()
