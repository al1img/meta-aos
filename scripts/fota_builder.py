#!/usr/bin/env python3
""" Create fota archive """
import argparse
import errno
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml


class FotaError(Exception):
    """Exception raised for errors during creating FOTA archive

    Attributes:
        error code -- Error code
        message -- explanation of the error
    """

    def __init__(self, error_code, message="FOTA error"):
        self.code = error_code
        self.message = f"{message} ({self.code})"

        super().__init__(self.message)


class FotaBuilder:
    """Create fota archive class"""

    def __init__(self, conf, verbose) -> None:
        self._conf = conf
        self._verbose = verbose
        self._bundle_name = conf["target_images"][0]
        self._metadata = {"formatVersion": 1, "components": []}
        self._bundle_dir = os.path.join(self._conf["work_dir"], "bundle")

    def create_bundle(self):
        """Create bundle from prepared files"""
        args = ["tar", "-cf", self._bundle_name, "-C", self._bundle_dir, "."]

        self._run_cmd(args)

    def process_component(self, component, conf):
        """Process component "component" """
        metadata = self._create_component_metadata(component, conf)

        method = conf["method"]

        if method == "raw":
            self._do_raw_component(metadata, conf)
        elif method == "overlay":
            self._do_overlay_component(metadata, conf)
        elif method == "custom":
            self._do_custom_component(metadata, conf)

        self._metadata["components"].append(metadata)

    def create_manifest(self):
        """Create manifest file in bundle directory"""
        manifest_file_name = os.path.join(self._bundle_dir, "manifest.json")

        with open(manifest_file_name, "w", encoding="utf-8") as manifest_file:
            json.dump(self._metadata, manifest_file, indent=4)

    def get_components(self):
        """Get dictionary with components configuration"""
        return self._conf["components"]

    def prepare_bundle_dir(self):
        """Prepare empty temporary dir for bundle"""
        self._prepare_dir(self._bundle_dir)

    def _run_cmd(self, args):
        if self._verbose:
            print("Running:", " ".join(args))

        ret = subprocess.run(args, check=True)
        if ret.returncode != 0:
            raise FotaError(ret.returncode)

    def _do_copy(self, src, dst):
        if self._verbose:
            print(f"Copy from {src} to {dst}")

        args = ["cp", "-Lf", src, dst]

        self._run_cmd(args)

    def _prepare_dir(self, wdir):
        if os.path.exists(wdir):
            if os.path.isfile(wdir):
                os.remove(wdir)
            else:
                shutil.rmtree(wdir)

        os.makedirs(wdir, exist_ok=True)

    def _copy(self, conf, file_name, dst_dir):
        path, _ = os.path.split(file_name)
        dst = os.path.join(dst_dir, file_name)
        src = conf[file_name]

        self._mkdir(dst_dir, path)
        self._do_copy(src, dst)

    def _copy_files(self, conf, dst_dir):
        for dst_file in conf.keys():
            src = conf[dst_file]

            if dst_file[0] == os.sep:
                dst_file = dst_file[1:]

            path, _ = os.path.split(dst_file)
            dst = os.path.join(dst_dir, dst_file)

            self._mkdir(dst_dir, path)
            self._do_copy(src, dst)

    def _init_ostree_repo(self, repo_path, mode="archive"):
        self._prepare_dir(repo_path)

        args = ["ostree", f"--repo={repo_path}", "init", f"--mode={mode}"]

        self._run_cmd(args)

    def _ostree_commit(self, repo, rootfs_dir, version):
        now = datetime.now()
        date_time = now.strftime("%m/%d/%Y-%H:%M")
        args = [
            "ostree",
            f"--repo={repo}",
            "commit",
            f"--tree=dir={rootfs_dir}/",
            "--skip-if-unchanged",
            f"--branch={version}",
            f'--subject="{version}-{date_time}"',
        ]

        self._run_cmd(args)

    def _ostree_diff(self, repo, vendor_ver, ref_ver):
        args = ["ostree", f"--repo={repo}", "diff", f"{ref_ver}", f"{vendor_ver}"]
        result = subprocess.run(args, stdout=subprocess.PIPE, check=True)

        return [
            list(line.split()) for line in result.stdout.decode("utf-8").split("\n")
        ]

    def _mkdir(self, root, new_dir):
        if len(new_dir) == 0:
            return

        dirs = Path(new_dir).parts
        path = root

        for dir_component in dirs:
            path = os.path.join(path, dir_component)

            if not os.path.exists(path):
                os.mkdir(path)

    def _update_metadata_var(self, conf, varname, metadata):
        var = conf.get(varname)

        if var:
            metadata[varname] = var

    def _create_component_metadata(self, component, conf):
        metadata = {
            "id": conf.get("componentType", component),
            "vendorVersion": conf["vendorVersion"],
        }
        metadata["fileName"] = conf.get(
            "fileName", f'{metadata["id"]}-{metadata["vendorVersion"]}.img'
        )

        self._update_metadata_var(conf, "requiredVersion", metadata)
        self._update_metadata_var(conf, "minVersion", metadata)
        self._update_metadata_var(conf, "maxVersion", metadata)
        self._update_metadata_var(conf, "description", metadata)
        self._update_metadata_var(conf, "annotations", metadata)

        return metadata

    def _do_raw_component(self, metadata, conf):
        """Create archive for raw component"""
        component = metadata["id"]
        rootfs_dir = os.path.join(self._conf["work_dir"], f"rootfs_{component}")

        self._prepare_dir(rootfs_dir)

        fstype = conf["partition"].get("type", "ext4")

        if fstype not in ["ext4"]:
            raise FotaError(errno.EINVAL, f"unsupported partition type {fstype}")

        image_file = os.path.join(self._conf["work_dir"], f"{component}.{fstype}")
        gz_image = os.path.join(self._bundle_dir, metadata["fileName"])

        for file in conf["partition"]["items"].keys():
            if self._verbose:
                print(f'{file} -> {conf["partition"]["items"][file]}')

            self._copy(conf["partition"]["items"], file, rootfs_dir)

        self._exclude_items(rootfs_dir, conf)

        args = [
            "dd",
            "if=/dev/zero",
            f"of={image_file}",
            "bs=1M",
            f'count={str(conf["partition"]["size"])}',
        ]

        self._run_cmd(args)  # run dd

        if fstype == "ext4":
            args = [
                "mkfs.ext4",
                "-F",
                "-E",
                "root_owner=0:0",
            ]

            label = conf["partition"].get("label")

            if label:
                args.extend(["-L", label])

            gpt = conf["partition"].get("gpt_type")

            if gpt:
                args.extend(["-U", gpt])

            args.extend(["-d", rootfs_dir, image_file])

            self._run_cmd(args)  # run mkfs.ext4

        os.system(f"gzip < {image_file} > {gz_image}")

    def _overlay_full(self, metadata, src_dir):
        """Create full update"""
        args = [
            "mksquashfs",
            src_dir,
            os.path.join(self._bundle_dir, metadata["fileName"]),
            "-noappend",
            "-wildcards",
            "-all-root",
        ]

        self._run_cmd(args)  # run  mksquashfs

    def _overlay_incremental(self, metadata, src_dir):
        """Create incremental update"""
        vendor_ver = metadata["vendorVersion"]
        base_ver = metadata["requiredVersion"]
        repo = self._conf.get("ostree_repo", "ostree_repo")
        diff_dir = os.path.join(self._conf["work_dir"], f"diff_{metadata['id']}")

        self._prepare_dir(diff_dir)

        diffs = self._ostree_diff(repo, vendor_ver, base_ver)

        for line in diffs:
            if len(line) != 2:
                continue

            item = line[1]

            if item[0] == os.sep:
                item = item[1:]

            if line[0] == "A" or line[0] == "M":
                if os.path.isdir(os.path.join(src_dir, item)):
                    self._mkdir(diff_dir, item)
                else:
                    path, _ = os.path.split(item)
                    self._mkdir(diff_dir, path)
                    self._do_copy(
                        os.path.join(src_dir, item), os.path.join(diff_dir, item)
                    )
            elif line[0] == "D":
                path, _ = os.path.split(item)

                self._mkdir(diff_dir, path)
                os.mknod(os.path.join(diff_dir, item), stat.S_IFCHR, os.makedev(0, 0))

        list_dir = os.listdir(diff_dir)

        if len(list_dir) == 0:
            print("incremental roofs update is empty")

        args = [
            "mksquashfs",
            diff_dir,
            os.path.join(self._bundle_dir, metadata["fileName"]),
            "-noappend",
            "-wildcards",
            "-all-root",
        ]

        self._run_cmd(args)  # run  mksquashfs

    def _do_overlay_component(self, metadata, conf):
        component = metadata["id"]
        overlay_type = conf["type"]

        if self._verbose:
            print(f"Creating {overlay_type} overlay image for {component}")

        annotats = metadata.get("annotations", {})
        annotats["type"] = overlay_type
        metadata["annotations"] = annotats

        rootfs_dir = os.path.join(self._conf["work_dir"], f"rootfs_{component}")
        self._prepare_dir(rootfs_dir)
        args = ["tar", "-C", rootfs_dir, "-xjf", conf["rootfs"]]
        self._run_cmd(args)  # run tar

        items = conf.get("items")
        if items:
            self._copy_files(items, rootfs_dir)

        self._exclude_items(rootfs_dir, conf)

        repo = self._conf.get("ostree_repo", "ostree_repo")
        vendor_ver = metadata["vendorVersion"]

        if not os.path.isdir(os.path.join(repo, "refs")):
            self._init_ostree_repo(repo)

        self._ostree_commit(repo, rootfs_dir, vendor_ver)

        if overlay_type == "full":
            self._overlay_full(metadata, rootfs_dir)
        elif overlay_type == "incremental":
            self._overlay_incremental(metadata, rootfs_dir)
        else:
            raise FotaError(errno.EINVAL, f"unknown bundle type {type}")

    def _do_custom_component(self, metadata, conf):
        src = conf["file"]
        self._do_copy(src, os.path.join(self._bundle_dir, metadata["fileName"]))

    def _exclude_items(self, rootfs_dir, conf):
        exclude = conf.get("exclude")

        if not exclude:
            return

        for item in exclude:
            if self._verbose:
                print(f"Exclude item: {item}")

            if item[0] == os.sep:
                item = item[1:]
            os.system(f"rm -rf {os.path.join(rootfs_dir, item)}")


def main():
    """main procedure"""
    parser = argparse.ArgumentParser(description="fota")

    parser.add_argument(
        "conf", metavar="conf.yaml", type=str, help="YAML file with configuration"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output"
    )

    args = parser.parse_args()

    with open(args.conf, "r", encoding="utf-8") as config_file:
        conf = yaml.load(config_file, Loader=yaml.CLoader)

    builder = FotaBuilder(conf, args.verbose)

    try:
        builder.prepare_bundle_dir()

        components = builder.get_components()

        for component in components.keys():
            print(f"Processing {component}")

            if not components[component].get("enabled", True):
                if args.verbose:
                    print(f"{component} skipped")

                continue

            builder.process_component(component, components[component])

        builder.create_manifest()
        builder.create_bundle()
    except FotaError as err:
        print(err)

    return 0


if __name__ == "__main__":
    sys.exit(main())