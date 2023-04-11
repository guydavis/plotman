import argparse
import contextlib
import logging
import math
import os
import posixpath
import random
import re
import subprocess
import sys
import time
import typing
from datetime import datetime

import pendulum
import psutil
import texttable as tt

from plotman import configuration, job, manager, plot_util


root_logger = logging.getLogger()
disk_space_logger = logging.getLogger("disk_space")

_WINDOWS = sys.platform == "win32"

# TODO : write-protect and delete-protect archived plots


def spawn_archive_process(
    dir_cfg: configuration.Directories,
    arch_cfg: configuration.Archiving,
    log_cfg: configuration.Logging,
    all_jobs: typing.List[job.Job],
) -> typing.Tuple[typing.Union[bool, str, typing.Dict[str, object]], typing.List[str]]:
    """Spawns a new archive process using the command created
    in the archive() function. Returns archiving status and a log message to print."""

    log_messages = []
    archiving_status = None

    # Look for running archive jobs.  Be robust to finding more than one
    # even though the scheduler should only run one at a time.
    arch_jobs: typing.Dict[str, typing.Dict] = get_running_archive_jobs(arch_cfg)
    if len(arch_jobs):
        root_logger.info("Found {0} already running transfers...".format(len(arch_jobs)))
    (should_start, status_or_cmd, archive_log_messages) = archive(
        dir_cfg, arch_cfg, all_jobs, arch_jobs
    )
    log_messages.extend(archive_log_messages)
    if not should_start:
        archiving_status = status_or_cmd
    else:
        args: typing.Dict[str, object] = status_or_cmd  # type: ignore[assignment]

        log_file_path = log_cfg.create_transfer_log_path(time=pendulum.now())
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        log_messages.append("{0} Starting archive of {1} to {2}. See {3}".format(
            timestamp, args['env']['source'],args['env']['destination'], log_file_path))
        try:
            open_log_file = open(log_file_path, "x")
        except FileExistsError:
            log_messages.append(
                f"Archiving log file already exists, skipping attempt to start a"
                f" new archive transfer: {log_file_path!r}"
            )
            return (False, log_messages)
        except FileNotFoundError as e:
            message = (
                f"Unable to open log file.  Verify that the directory exists"
                f" and has proper write permissions: {log_file_path!r}"
            )
            raise Exception(message) from e

        # Preferably, do not add any code between the try block above
        # and the with block below.  IOW, this space intentionally left
        # blank...  As is, this provides a good chance that our handle
        # of the log file will get closed explicitly while still
        # allowing handling of just the log file opening error.

        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW
        else:
            creationflags = 0

        with open_log_file:
            # start_new_sessions to make the job independent of this controlling tty.
            p = subprocess.Popen(  # type: ignore[call-overload]
                **args,
                shell=True,
                stdout=open_log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                creationflags=creationflags,
            )
        time.sleep(3) # Wait for rsync to get running or fail

    if archiving_status is None:
        archiving_status = "Now {0} running transfers...".format(len(arch_jobs)+1)

    return archiving_status, log_messages


def compute_priority(phase: job.Phase, gb_free: float, n_plots: int) -> int:
    # All these values are designed around dst buffer dirs of about
    # ~2TB size and containing k32 plots.  TODO: Generalize, and
    # rewrite as a sort function.

    priority = 50

    # To avoid concurrent IO, we should not touch drives that
    # are about to receive a new plot.  If we don't know the phase,
    # ignore.
    if phase.known:
        if phase == job.Phase(3, 4):
            priority -= 4
        elif phase == job.Phase(3, 5):
            priority -= 8
        elif phase == job.Phase(3, 6):
            priority -= 16
        elif phase >= job.Phase(3, 7):
            priority -= 32

    # If a drive is getting full, we should prioritize it
    if gb_free < 1000:
        priority += 1 + int((1000 - gb_free) / 100)
    if gb_free < 500:
        priority += 1 + int((500 - gb_free) / 100)

    # Finally, least importantly, pick drives with more plots
    # over those with fewer.
    priority += n_plots

    return priority


def get_archdir_freebytes(
    arch_cfg: configuration.Archiving,
) -> typing.Tuple[typing.Dict[str, int], typing.List[str]]:
    log_messages = []
    target = arch_cfg.target_definition()

    archdir_freebytes = {}
    timeout = 5
    try:
        completed_process = subprocess.run(
            [target.disk_space_path],  # type: ignore[list-item]
            env={**os.environ, **arch_cfg.environment()},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        log_messages.append(f"Disk space check timed out in {timeout} seconds")
        if e.stdout is None:
            stdout = ""
        else:
            stdout = e.stdout.decode("utf-8", errors="ignore").strip()
        if e.stderr is None:
            stderr = ""
        else:
            stderr = e.stderr.decode("utf-8", errors="ignore").strip()
    else:
        stdout = completed_process.stdout.decode("utf-8", errors="ignore").strip()
        stderr = completed_process.stderr.decode("utf-8", errors="ignore").strip()
        for line in stdout.splitlines():
            line = line.strip()
            split = line.split(":")
            if len(split) != 2:
                log_messages.append(f"Unable to parse disk script line: {line!r}")
                continue
            archdir, space = split
            freebytes = int(space)
            archdir_freebytes[archdir.strip()] = freebytes

    for line in log_messages:
        disk_space_logger.info(line)

    disk_space_logger.info("stdout from disk space script:")
    for line in stdout.splitlines():
        disk_space_logger.info(f"    {line}")

    disk_space_logger.info("stderr from disk space script:")
    for line in stderr.splitlines():
        disk_space_logger.info(f"    {line}")

    return archdir_freebytes, log_messages


# TODO: maybe consolidate with similar code in job.py?
def get_running_archive_jobs(arch_cfg: configuration.Archiving) -> typing.Dict[str, typing.Dict]:
    """Look for running rsync jobs that seem to match the pattern we use for archiving
    them.  Return a list of PIDs of matching jobs."""
    jobs = {}
    target = arch_cfg.target_definition()
    variables = {**os.environ, **arch_cfg.environment()}
    proc_name = target.transfer_process_name.format(**variables)
    for proc in psutil.process_iter():
        with contextlib.suppress(psutil.NoSuchProcess):
            with proc.oneshot():
                if proc.name() == proc_name: # rsync
                    args = proc.cmdline()
                    #root_logger.info(args)
                    for arg in args:
                        if arg.endswith(".plot"):
                            source = args[-2] # 2nd last arg is the source file
                            dest = args[-1] # Last arg is the destination
                            #root_logger.info('Adding current job source: {0}'.format(source))
                            jobs[source] = { 'pid': proc.pid, 'dest': dest }
    if len(jobs):
        root_logger.info("Currenly running transfers: {0}".format(jobs))
    return jobs


def archive(
    dir_cfg: configuration.Directories,
    arch_cfg: configuration.Archiving,
    all_jobs: typing.List[job.Job],
    arch_jobs: typing.Dict[str, typing.Dict]
) -> typing.Tuple[
    bool, typing.Optional[typing.Union[typing.Dict[str, object], str]], typing.List[str]
]:
    """Configure one archive job.  Needs to know all jobs so it can avoid IO
    contention on the plotting dstdir drives.  Returns either (False, <reason>)
    if we should not execute an archive job or (True, <cmd>) with the archive
    command if we should."""
    log_messages: typing.List[str] = []
    if arch_cfg is None:
        return (False, "No 'archive' settings declared in plotman.yaml", log_messages)

    dir2ph = manager.dstdirs_to_furthest_phase(all_jobs)
    best_priority = -100000000
    chosen_plot = None
    dst_dir = dir_cfg.get_dst_directories()
    for d in dst_dir:
        ph = dir2ph.get(d, job.Phase(0, 0))
        dir_plots = plot_util.list_plots(d)  # All plots that could be transferred
        candidate_plots = [x for x in dir_plots if x not in arch_jobs.keys()]  # All not yet being transferred
        gb_free = plot_util.df_b(d) / plot_util.GB
        n_plots = len(candidate_plots)
        priority = compute_priority(ph, gb_free, n_plots)
        if priority >= best_priority and candidate_plots:
            best_priority = priority
            chosen_plot = candidate_plots[0]

    if not chosen_plot:
        return (False, "No candidate plots found to transfer.", log_messages)
    
    # TODO: sanity check that archive machine is available
    # TODO: filter drives mounted RO

    #
    # Pick first archive dir with sufficient space
    #
    archdir_freebytes, freebytes_log_messages = get_archdir_freebytes(arch_cfg)
    log_messages.extend(freebytes_log_messages)
    if not archdir_freebytes:
        return (False, "No free archive dirs found.", log_messages)

    archdir = ""
    chosen_plot_size = os.stat(chosen_plot).st_size
    # 10MB is big enough to outsize filesystem block sizes hopefully, but small
    # enough to make this a pretty tight corner for people to get stuck in.
    free_space_margin = 10_000_000
    currently_used_dests = []
    for source in arch_jobs:
        currently_used_dests.append(os.path.basename(os.path.normpath(arch_jobs[source]['dest'])))
    root_logger.info("Currently used destinations {0}".format(currently_used_dests))
    root_logger.info("All available destinations {0}".format(archdir_freebytes.items()))
    available = []
    for (d, space) in archdir_freebytes.items():
        if space > (chosen_plot_size + free_space_margin):
            candidate_dest = os.path.basename(os.path.normpath('{0}/'.format(d)))
            dest_is_currently_used = False
            for currently_used_dest in currently_used_dests:
                root_logger.info("Does used dest '{0}' match candidate dest '{1}'?".format(currently_used_dest, candidate_dest))
                if currently_used_dest == candidate_dest:
                    dest_is_currently_used = True
            if not dest_is_currently_used:
                root_logger.info("Keeping {0} as not currently used.".format(d))
                available.append((d,space))
            else:
                root_logger.info("Dropping {0} as currently used.".format(d))
                pass
    root_logger.info("Remaining available targets {0}".format(available))
    if len(available) > 0:
        index = arch_cfg.index % len(available)
        (archdir, freespace) = sorted(available)[index]

    if not archdir:
        if len(arch_jobs):
            return (
                False,
                "No available archive directories without an active transfer already.",
                log_messages,
            )
        else:
            return (
                False,
                "No available archive directories found with enough free space.",
                log_messages,
            )

    env = arch_cfg.environment(
        source=chosen_plot,
        destination=archdir,
    )
    subprocess_arguments: typing.Dict[str, object] = {
        "args": arch_cfg.target_definition().transfer_path,
        "env": {**os.environ, **env},
    }

    return (True, subprocess_arguments, log_messages)
