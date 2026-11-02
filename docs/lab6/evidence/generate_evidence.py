"""Reproduce the fixed Lab 6 interval using read-only Git commands."""

import csv
import io
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent
BASE = "a33d21d1a80b6ce959e286e993d49da99e6a5d28"
FINAL = "24946dd8873615ab01a86bde9eed839f0307b98f"
ITERATIONS_FINAL = "cfb1f0423e8959a2d232f427f0aa355811947eec"
A = "a82816f5c238ecc916f82f043c11874c4a718b8e"
B = "bedb7ec93614547f644fcc9585d60ab80b4b07ad"
MERGE_A = "fbaa40aa970027d218eaf41202b6c920f3dead45"
RESOLUTION = "59786e81ccc55b978aaae3bac9b91ccd03c855d7"
DOCUMENTATION = "66879b0ab34e2bf1d705d3bc806e319446303d1f"
FORMAT = "%H%nParents: %P%nAuthor: %an%nAuthor date: %aI%nCommit date: %cI%nSubject: %s"
EXPECTED_PARENTS = {
    "0ed2111e23e85c3e6aaf6e4f55d6392d4a299a1b": [BASE],
    "42ffbf5d453d57d3b05afacad9cd2cc1f57bd350": [
        BASE, "0ed2111e23e85c3e6aaf6e4f55d6392d4a299a1b",
    ],
    "387d661993e98203e861de52e1978fa94eba01f1": [
        "42ffbf5d453d57d3b05afacad9cd2cc1f57bd350",
    ],
    "4ebc1277819996a76f5e66e2b2aaa5723eee3e82": [
        "42ffbf5d453d57d3b05afacad9cd2cc1f57bd350",
        "387d661993e98203e861de52e1978fa94eba01f1",
    ],
    "ee7816bed74fe73dde1f0b71b0c4e0d107ccebad": [
        "4ebc1277819996a76f5e66e2b2aaa5723eee3e82",
    ],
    ITERATIONS_FINAL: [
        "4ebc1277819996a76f5e66e2b2aaa5723eee3e82",
        "ee7816bed74fe73dde1f0b71b0c4e0d107ccebad",
    ],
    A: [ITERATIONS_FINAL],
    B: [ITERATIONS_FINAL],
    MERGE_A: [ITERATIONS_FINAL, A],
    RESOLUTION: [MERGE_A, B],
    DOCUMENTATION: [RESOLUTION],
    FINAL: [ITERATIONS_FINAL, DOCUMENTATION],
}


def git(*args):
    return subprocess.run(
        ["git", "--no-pager", "-c", "core.quotepath=false", *args],
        cwd=ROOT, check=True, capture_output=True, encoding="utf-8",
    ).stdout


def save(name, text):
    text = "\n".join(line.rstrip() for line in text.splitlines())
    (OUTPUT / name).write_text(text.rstrip() + "\n", encoding="utf-8")


def capture(*args):
    return "$ git " + subprocess.list2cmdline(args) + "\n" + git(*args)


def ranking(end):
    interval = f"{BASE}..{end}"
    commits = git("rev-list", "--no-merges", interval).splitlines()
    files = {}
    renames = []
    for commit in commits:
        tokens = git(
            "diff-tree", "--no-commit-id", "--name-status", "-r", "-M", "-z", commit,
        ).split("\0")
        touched = set()
        index = 0
        while index < len(tokens) and tokens[index]:
            status, path = tokens[index:index + 2]
            index += 2
            if status.startswith(("R", "C")):
                destination = tokens[index]
                index += 1
                renames.append((commit, status, path, destination))
                path = destination
            touched.add(path)
        for path in touched:
            files.setdefault(path, set()).add(commit)
    return sorted(files.items(), key=lambda item: (-len(item[1]), item[0])), renames, commits


def save_ranking(name, rows):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["rank", "file", "non_merge_commits", "commit_shas"])
    previous_count = None
    rank = 0
    for index, (path, commits) in enumerate(rows, 1):
        if len(commits) != previous_count:
            rank = index
            previous_count = len(commits)
        writer.writerow([rank, path, len(commits), ";".join(sorted(commits))])
    save(name, buffer.getvalue())


def main():
    for commit, parents in EXPECTED_PARENTS.items():
        actual = git("show", "-s", "--format=%P", commit).strip().split()
        if actual != parents:
            raise RuntimeError(f"Unexpected parents for {commit}: {actual}")
    git("merge-base", "--is-ancestor", BASE, FINAL)
    interval = f"{BASE}..{FINAL}"
    rows, renames, commits = ranking(FINAL)
    iteration_rows, iteration_renames, iteration_commits = ranking(ITERATIONS_FINAL)
    if not rows:
        raise RuntimeError("The fixed academic interval contains no changed files")
    save_ranking("file-change-ranking.csv", rows)
    save_ranking("iteration-file-change-ranking.csv", iteration_rows)
    winners = [path for path, hashes in rows if len(hashes) == len(rows[0][1])]
    selected = winners[0]
    save("git-graph.txt", capture(
        "log", "--graph", "--boundary", "--topo-order", "--format=" + FORMAT, interval,
    ))
    save("merge-log.txt", capture(
        "log", "--merges", "--topo-order", "--format=" + FORMAT, interval,
    ))
    topology = "\n".join(
        capture("show", "-s", "--format=" + FORMAT, commit)
        for commit in [ITERATIONS_FINAL, A, B, MERGE_A, RESOLUTION, DOCUMENTATION, FINAL]
    )
    save("simulation-topology.txt", topology)
    save("most-changed-file-log.txt", (
        f"Interval: {interval}\n"
        f"Maximum distinct non-merge commits: {len(rows[0][1])}\n"
        f"Tied paths: {', '.join(winners)}\n"
        f"Selected: {selected} (lexicographically first if tied)\n\n"
        + capture(
            "log", "--full-history", "--no-merges", "--topo-order",
            "--format=" + FORMAT, "--stat", "-M", interval, "--", selected,
        )
    ))
    save("most-changed-file-with-merges.txt", (
        "Complementary history; -m repeats a merge once per relevant parent.\n"
        "Do not use these repeated records for the principal ranking.\n\n"
        + capture(
            "log", "--full-history", "-m", "--topo-order", "--format=" + FORMAT,
            "--stat", "-M", interval, "--", selected,
        )
    ))
    resolution = capture("show", "-s", "--format=" + FORMAT, RESOLUTION)
    for parent in [MERGE_A, B]:
        resolution += "\n" + capture(
            "diff", "--stat", parent, RESOLUTION, "--", "app/ui/app.js",
        )
        resolution += "\n" + capture(
            "diff", parent, RESOLUTION, "--", "app/ui/app.js",
        )
    remerge = capture(
        "show", "--remerge-diff", "--format=" + FORMAT, RESOLUTION, "--", "app/ui/app.js",
    )
    resolution += (
        "\nReconstructed Git remerge diff (not the original terminal transcript).\n"
        "Each line below is prefixed with '| ' to preserve archival conflict markers safely.\n"
        + "\n".join("| " + line for line in remerge.splitlines())
    )
    save("conflict-resolution-log.txt", resolution)
    # Inspect rename detection in merges too, separately from the principal count.
    rename_log = capture(
        "log", "--full-history", "-m", "--format=" + FORMAT, "--name-status",
        "--diff-filter=R", "-M", interval,
    )
    save("rename-inspection.txt", (
        f"Principal rename records: {renames!r}\n"
        f"Three-iteration rename records: {iteration_renames!r}\n"
        "Rename detection uses Git -M (default 50% similarity).\n"
        "If a rename occurs, count its destination path; do not silently merge path histories.\n\n"
        + rename_log
    ))
    save("analysis-summary.txt", (
        f"BASE: {BASE}\nFINAL: {FINAL}\n"
        f"All reachable commits in interval: {len(git('rev-list', interval).splitlines())}\n"
        f"Non-merge commits: {len(commits)}\n"
        f"Merge commits: {len(git('rev-list', '--merges', interval).splitlines())}\n"
        f"Distinct commit/file pairs: {sum(len(hashes) for _, hashes in rows)}\n"
        f"Distinct paths: {len(rows)}\n"
        f"Selected path: {selected}\n"
        f"Maximum count: {len(rows[0][1])}\n"
        f"Ties: {winners!r}\n"
        f"Three-iteration FINAL: {ITERATIONS_FINAL}\n"
        f"Three-iteration non-merge commits: {len(iteration_commits)}\n"
        f"Three-iteration ranking: {[(path, len(hashes)) for path, hashes in iteration_rows]!r}\n"
        f"Verified explicit parent lists: {len(EXPECTED_PARENTS)}\n"
    ))
    print((OUTPUT / "analysis-summary.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
