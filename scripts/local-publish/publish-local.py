#!/usr/bin/env python3
"""Run the SDK npm publish workflow on this machine.

The source of truth is `.github/workflows/publish.yml` in a platform checkout.
This script reads that file at run time and executes the `run:` steps of the
publish job in order, once per matrix package, with the same working
directories and step outputs. `uses:` steps are not run; they become checks
on this machine (Node major, pnpm version, checkout). Anything the script does
not understand fails loudly, so a change to the YAML that it cannot follow
shows up here instead of silently diverging.

Deliberate differences from CI:
  - Auth is an npm token from NPM_TOKEN. OIDC trusted publishing only exists
    inside GitHub Actions.
  - `--provenance` is removed from `npm publish`, since provenance needs the
    GitHub OIDC token. Tarballs published from here have no attestation.
  - Nothing is published unless --publish is given. Without it, every
    `npm publish` runs as `npm publish --dry-run`.

Usage:
  ./publish-local.py --platform-dir ~/dev/stores/fgs/platform
  ./publish-local.py --platform-dir ~/dev/stores/fgs/platform --package compliance
  NPM_TOKEN=... ./publish-local.py --platform-dir ~/dev/stores/fgs/platform --publish
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

try:
    import yaml
except ImportError:
    sys.exit("publish-local: PyYAML is required. Install with: python3 -m pip install --user pyyaml")

EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
IF_OUTPUT = re.compile(r"^steps\.([\w-]+)\.outputs\.([\w-]+)\s*==\s*'([^']*)'$")
KNOWN_STEP_KEYS = {"name", "id", "if", "run", "working-directory", "env"}


class StepFailed(Exception):
    def __init__(self, label, rc):
        super().__init__(label)
        self.label = label
        self.rc = rc


def die(msg):
    sys.exit(f"publish-local: {msg}")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def tool_version(cmd, cwd):
    # Run inside the checkout: corepack picks pnpm from the repo's packageManager pin.
    return subprocess.run([cmd, "--version"], cwd=cwd, capture_output=True, text=True).stdout.strip()


def preflight(platform_dir, allow_dirty):
    if git(platform_dir, "branch", "--show-current") != "main":
        die(f"{platform_dir} is not on main; publish.yml only publishes from main")
    if not allow_dirty and git(platform_dir, "status", "--porcelain"):
        die("platform checkout has uncommitted changes (pass --allow-dirty to override)")
    print(f"publish-local: source {platform_dir} @ {git(platform_dir, 'rev-parse', '--short', 'HEAD')}")


def check_environment(steps, platform_dir):
    """Turn the job's `uses:` steps into checks against this machine."""
    for step in steps:
        uses = step.get("uses")
        if not uses:
            continue
        action = uses.split("@")[0]
        if action == "actions/checkout":
            continue  # the platform checkout is the source
        if action == "actions/setup-node":
            want = str(step.get("with", {}).get("node-version", ""))
            have = tool_version("node", platform_dir)
            if not have.startswith(f"v{want}."):
                die(f"node {have or 'missing'} found; workflow wants {want}.x")
        elif action == "pnpm/action-setup":
            with open(os.path.join(platform_dir, "package.json")) as f:
                pin = json.load(f).get("packageManager", "")
            if not pin.startswith("pnpm@"):
                die("package.json has no pnpm packageManager pin to match")
            want = pin[len("pnpm@"):]
            have = tool_version("pnpm", platform_dir)
            if have != want:
                die(f"pnpm {have or 'missing'} found; package.json pins {want} (run `corepack enable`)")
        else:
            die(f"unsupported action {uses}: add an explicit local check for it in publish-local.py")


def subst(text, matrix):
    """Replace ${{ matrix.<key> }} and refuse any other GitHub expression."""
    def repl(m):
        expr = m.group(1)
        if expr.startswith("matrix.") and expr[len("matrix."):] in matrix:
            return matrix[expr[len("matrix."):]]
        die(f"unsupported expression ${{{{ {expr} }}}} in publish.yml")
    return EXPR.sub(repl, str(text))


def localize_publish(body, publish):
    """Adapt `npm publish` lines for a laptop: no provenance, dry-run unless --publish."""
    out = []
    for line in body.splitlines(keepends=True):
        if "npm publish" in line:
            line = line.replace(" --provenance", "")
            if not publish:
                line = line.replace("npm publish", "npm publish --dry-run", 1)
        out.append(line)
    return "".join(out)


def parse_outputs(path):
    out = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                key, sep, value = line.rstrip("\n").partition("=")
                if sep:
                    out[key] = value
    return out


def run_steps(steps, platform_dir, matrix, env, publish, tmp):
    outputs = {}
    for i, step in enumerate(steps):
        if "uses" in step:
            continue
        label = step.get("name") or step.get("id") or f"step {i}"
        unknown = set(step) - KNOWN_STEP_KEYS
        if unknown:
            die(f"step {label!r} uses keys this script does not handle: {sorted(unknown)}")
        if "if" in step:
            cond = subst(step["if"], matrix)
            m = IF_OUTPUT.match(cond)
            if not m:
                die(f"unsupported if in step {label!r}: {cond!r}")
            if outputs.get(m[1], {}).get(m[2]) != m[3]:
                print(f"-- skipped: {label}")
                continue
        if "run" not in step:
            die(f"step {label!r} has neither run nor uses")

        cwd = os.path.join(platform_dir, subst(step.get("working-directory", "."), matrix))
        body = localize_publish(subst(step["run"], matrix), publish)
        step_env = dict(env)
        for key, value in step.get("env", {}).items():
            step_env[key] = subst(value, matrix)
        out_path = os.path.join(tmp, f"output-{i}")
        step_env["GITHUB_OUTPUT"] = out_path

        print(f"\n== {label}")
        rc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", body],
            cwd=cwd, env=step_env,
        ).returncode
        if rc:
            raise StepFailed(label, rc)
        if "id" in step:
            outputs[step["id"]] = parse_outputs(out_path)


def write_npmrc(tmp, token):
    path = os.path.join(tmp, "npmrc")
    lines = ["registry=https://registry.npmjs.org/"]
    if token:
        lines.append("//registry.npmjs.org/:_authToken=${NODE_AUTH_TOKEN}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


def main():
    ap = argparse.ArgumentParser(description="Run platform publish.yml locally.")
    ap.add_argument("--platform-dir", default=os.environ.get("FGS_PLATFORM_DIR"),
                    help="platform checkout (default: $FGS_PLATFORM_DIR)")
    ap.add_argument("--workflow", default=".github/workflows/publish.yml")
    ap.add_argument("--job", default="publish")
    ap.add_argument("--package", action="append", help="only run this matrix package (repeatable)")
    ap.add_argument("--publish", action="store_true", help="really publish; default is dry-run")
    ap.add_argument("--allow-dirty", action="store_true", help="allow an uncommitted platform checkout")
    args = ap.parse_args()

    if not args.platform_dir:
        die("pass --platform-dir or set FGS_PLATFORM_DIR")
    platform_dir = os.path.abspath(os.path.expanduser(args.platform_dir))
    workflow_path = os.path.join(platform_dir, args.workflow)
    if not os.path.isfile(workflow_path):
        die(f"no workflow at {workflow_path}")

    token = os.environ.get("NPM_TOKEN", "")
    if args.publish and not token:
        die("--publish needs NPM_TOKEN in the environment")

    with open(workflow_path) as f:
        workflow = yaml.safe_load(f)
    job = workflow.get("jobs", {}).get(args.job)
    if not job:
        die(f"job {args.job!r} not found in {args.workflow}")
    if "if" in job or "needs" in job:
        die("job-level if/needs are not handled by publish-local.py")

    matrix_def = job.get("strategy", {}).get("matrix", {})
    if set(matrix_def) - {"package"}:
        die(f"unsupported matrix keys: {sorted(set(matrix_def) - {'package'})}")
    packages = matrix_def.get("package", [None])
    if args.package:
        matched = [p for p in packages if p in args.package]
        if not matched:
            die(f"--package {args.package} matched nothing in matrix {packages}")
        packages = matched
    fail_fast = job.get("strategy", {}).get("fail-fast", True)

    preflight(platform_dir, args.allow_dirty)
    check_environment(job["steps"], platform_dir)
    print(f"publish-local: mode {'PUBLISH' if args.publish else 'dry-run'}; packages {packages}")
    if args.publish:
        print("publish-local: npm publish runs without --provenance (no GitHub OIDC on this machine)")

    tmp = tempfile.mkdtemp(prefix="publish-local-")
    try:
        npmrc = write_npmrc(tmp, token)
        env = dict(os.environ, npm_config_userconfig=npmrc, NODE_AUTH_TOKEN=token)
        results = {}
        for pkg in packages:
            matrix = {"package": pkg} if pkg is not None else {}
            print(f"\n### {pkg or 'job'}")
            try:
                run_steps(job["steps"], platform_dir, matrix, env, args.publish, tmp)
                results[pkg] = "ok"
            except StepFailed as e:
                results[pkg] = f"failed at {e.label!r} (exit {e.rc})"
                if fail_fast:
                    break
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\npublish-local summary:")
    for pkg, status in results.items():
        print(f"  {pkg or 'job'}: {status}")
    sys.exit(1 if any(s != "ok" for s in results.values()) else 0)


if __name__ == "__main__":
    main()
