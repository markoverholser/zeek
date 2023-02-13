#!/usr/bin/env python3
"""
Collect Git information from the Zeek repository and output a JSON
document on stdout for inclusion into the executable.

Example usage:

    ./ci/collect-repo-info.py './auxil/spicy-plugin'
"""

import argparse
import json
import logging
import pathlib
import os
import subprocess
import sys

GIT = "git"

logger = logging.getLogger(__name__)


def git(*args):
    return subprocess.check_output([GIT, *args]).decode("utf-8")


def git_is_dirty(d: pathlib.Path):
    return (len(git("-C", str(d), "status", "--untracked=no", "--short").splitlines()) > 0)


def git_generic_info(d: pathlib.Path):
    """
    Collect git information from directory d
    """
    info = {
        "commit": git("-C", str(d), "rev-list", "-1", "HEAD").strip(),
        "dirty": git_is_dirty(d),
    }

    # git describe fails on Cirrus CI due to no tags being available
    # in the shallow clone. Instead of using --all, just skip over it.
    try:
        info["describe"] = git("-C", str(d), "describe", "--tags").strip()
    except subprocess.CalledProcessError:
        if "CIRRUS_CI" not in os.environ:
            logger.warning("Could not git describe %s", d)

    return info


def collect_submodule_info(zeek_dir: pathlib.Path):
    submodules = []
    for sm in git("-C", str(zeek_dir), "submodule", "status").splitlines():
        sm = sm.strip()
        if sm.count(" ") != 2:
            logger.error("submodules not updated: %s", sm)
            sys.exit(1)

        commit, path, describe = sm.split(" ")
        flag = None
        if commit[0] in "U+-":
            flag = commit[0]
            commit = commit[1:]

        describe = describe.strip("()")
        sm_info = {
            "path": path,
            "commit": commit,
            "describe": describe,
            "dirty": git_is_dirty(pathlib.Path(zeek_dir / path)),
        }
        if flag:
            sm_info["flag"] = flag

        try:
            sm_info["version"] = (zeek_dir / path / "VERSION").read_text().strip()
        except FileNotFoundError:
            # The external ones usually don't have a version.
            pass

        submodules.append(sm_info)

    return submodules


def collect_git_info(zeek_dir: pathlib.Path):
    """
    Assume we have a git checkout.
    """
    info = git_generic_info(zeek_dir)
    info["name"] = "zeek"
    info["version"] = (zeek_dir / "VERSION").read_text().strip()
    info["submodules"] = collect_submodule_info(zeek_dir)
    info["branch"] = git("-C", str(zeek_dir), "rev-parse", "--abbrev-ref", "HEAD").strip()
    info["source"] = "git"

    return info


def collect_plugin_info(plugin_dir: pathlib.Path):
    """ """
    # A plugin's name is not part of it's metadata/information, use
    # the basename of its directory.
    result = {
        "name": plugin_dir.parts[-1],
    }

    try:
        result["version"] = (plugin_dir / "VERSION").read_text().strip()
    except FileNotFoundError:
        logger.warning("No VERSION found in %s", plugin_dir)

    if (plugin_dir / ".git").exists():
        result.update(git_generic_info(plugin_dir))

    return result


def main():
    parser = argparse.ArgumentParser()

    def included_plugin_dir_conv(v):
        for p in [p.strip() for p in v.split(";") if p.strip()]:
            yield pathlib.Path(p)

    parser.add_argument("included_plugin_dirs",
                        default="",
                        nargs="?",
                        type=included_plugin_dir_conv)
    parser.add_argument("--dir", default=".")
    parser.add_argument("--only-git",
                        action="store_true",
                        help="Do not try repo-info.json fallback")
    args = parser.parse_args()

    logging.basicConfig(format="%(levelname)s: %(message)s")

    zeek_dir = pathlib.Path(args.dir).absolute()

    if not (zeek_dir / "zeek-config.h.in").exists():
        logger.error("%s missing zeek-config.h.in", zeek_dir)
        return 1

    try:
        git("--version")
    except OSError as e:
        logger.error("No git? (%s)", str(e))
        return 1

    # Attempt to collect info from git first and alternatively
    # fall back to a repo-info.json file within what is assumed
    # to be a tarball.
    if (zeek_dir / ".git").is_dir():
        info = collect_git_info(zeek_dir)
    elif not args.only_git:
        try:
            with open(zeek_dir / "repo-info.json") as fp:
                info = json.load(fp)
                info["source"] = "repo-info.json"
        except FileNotFoundError:
            logger.error("%s is not a git repo and repo-info.json missing", zeek_dir)
            return 1
    else:
        logger.error("Not a git repo and --only-git provided")
        return 1

    included_plugins_info = []
    for plugin_dir in args.included_plugin_dirs:
        if not plugin_dir.is_dir():
            logger.error("Plugin directory %s does not exist", plugin_dir)
            return 1

        included_plugins_info.append(collect_plugin_info(plugin_dir))

    info["included_plugins"] = included_plugins_info

    json_str = json.dumps(info, indent=2, sort_keys=True)
    print(json_str)


if __name__ == "__main__":
    sys.exit(main())
