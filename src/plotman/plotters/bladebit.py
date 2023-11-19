# mypy: allow_untyped_decorators

import collections
import os
import pathlib
import subprocess
import typing

import attr
import click
import packaging.version
import pendulum

import plotman.job
import plotman.plotters


@attr.frozen
class Options:
    executable: str = "bladebit"
    n: int = 1
    k: int = 32 # TODO Watch for BB to support k > 32 eventually...
    threads: typing.Optional[int] = None
    no_numa: bool = False
    mode: str = "ramplot"
    diskplot_cache: typing.Optional[str] = None
    diskplot_buckets: typing.Optional[int] = None
    f1threads: typing.Optional[int] = None
    fpthreads: typing.Optional[int] = None
    cthreads: typing.Optional[int] = None
    p2threads: typing.Optional[int] = None
    p3threads: typing.Optional[int] = None
    compression: int = 0
    diskplot: bool = False  # Deprecated
    disk_128: bool = False
    disk_16: bool = False
    no_direct_io: bool = False
    check_plot: bool = False                # Perform a plot check for <n> proofs on the newly created plot.
    check-threshold: bool = False           # Proof threshold rate below which the plots that don't pass the check will be deleted.  That is, the number of proofs fetched / proof check count  must be above or equal to this threshold to pass. (default=0.6).

    def chosen_executable(self) -> str:
        if self.mode == 'gpuplot':
            return "bladebit_cuda"
        return self.executable

def check_configuration(
    options: Options, pool_contract_address: typing.Optional[str]
) -> None:
    completed_process = subprocess.run(
        args=[options.chosen_executable(), "--version"],
        capture_output=True,
        check=True,
        encoding="utf-8",
    )
    if pool_contract_address is not None:
        completed_process = subprocess.run(
            args=[options.chosen_executable(), "--help"],
            capture_output=True,
            check=True,
            encoding="utf-8",
        )
        # TODO: report upstream
        if (
            "--pool-contract" not in completed_process.stdout
            and "--pool-contract" not in completed_process.stderr
        ):
            print(completed_process.stdout)
            raise Exception(
                f"found BladeBit version does not support the `--pool-contract`"
                f" option for pools."
            )


def create_command_line(
    options: Options,
    tmpdir: str,
    tmp2dir: typing.Optional[str],
    dstdir: str,
    farmer_public_key: typing.Optional[str],
    pool_public_key: typing.Optional[str],
    pool_contract_address: typing.Optional[str],
) -> typing.List[str]:
    args = [
        options.chosen_executable(),
        "-v",
        "-n",
        str(options.n),
    ]

    if options.threads is not None:
        args.append("-t")
        args.append(str(options.threads))

    if farmer_public_key is not None:
        args.append("-f")
        args.append(farmer_public_key)
    if pool_public_key is not None:
        args.append("-p")
        args.append(pool_public_key)
    if pool_contract_address is not None:
        args.append("-c")
        args.append(pool_contract_address)

    if options.no_numa:
        args.append("--no-numa")
    if options.no_direct_io:
        args.append("--no-direct-io")

    args.append("--compress")
    args.append(str(options.compression))

    if options.mode == 'diskplot':
        args.append("diskplot")
    elif options.mode == 'gpuplot':
        args.append("cudaplot")
    elif options.mode == 'ramplot':
        if options.diskplot:
            print("Found deprecated 'diskplot: true' option.  Please use 'mode: diskplot' instead.")
            args.append("diskplot")
        else:
            args.append("ramplot")  # Default for bladebit was always in-memory only
    else:
        raise Exception('Unknown mode configured: {0}'.format(options.mode))

    if options.mode == 'diskplot'and options.diskplot_cache:
        args.append("--cache")
        args.append(options.diskplot_cache)
    
    if options.mode == 'diskplot' and options.diskplot_buckets:
        args.append("--buckets")
        args.append(str(options.diskplot_buckets))

    if options.mode == 'diskplot' and options.f1threads:
        args.append("--f1-threads")
        args.append(str(options.f1threads))

    if options.mode == 'diskplot' and options.fpthreads:
        args.append("--fp-threads")
        args.append(str(options.fpthreads))

    if options.mode == 'diskplot' and options.cthreads:
        args.append("--c-threads")
        args.append(str(options.cthreads))

    if options.mode == 'diskplot' and options.p2threads:
        args.append("--p2-threads")
        args.append(str(options.p2threads))

    if options.mode == 'diskplot' and options.p3threads:
        args.append("--p3-threads")
        args.append(str(options.p3threads))
    
    if options.mode == 'gpuplot':
        if options.disk_128:
            args.append("--disk-128")
        elif options.disk_16:
            args.append("--disk-16")
            
    if options.mode == "gpuplot" and options.check_plot:
            args.append("--check 100")
            # args.append("--check-threshold 0.6")   # Automated plot deletion when plotcheck threshhold is below 0.6. Not yet used by us.

    if options.mode == 'diskplot' and tmpdir is not None:
        args.append("-t1")
        args.append(tmpdir)
    
    if options.mode == 'diskplot' and tmp2dir is not None:
        args.append("-t2")
        args.append(tmp2dir)

    if options.mode == 'gpuplot' and (options.disk_128 or options.disk_16):
        if tmpdir is not None:
            args.append("-t1")
            args.append(tmpdir)
        if tmp2dir is not None:
            args.append("-t2")
            args.append(tmp2dir)

    args.append(dstdir)
    return args


@plotman.plotters.check_SpecificInfo
@attr.frozen
class SpecificInfo:
    process_id: typing.Optional[int] = None
    phase: plotman.job.Phase = plotman.job.Phase(known=False)

    started_at: typing.Optional[pendulum.DateTime] = None
    plot_id: str = ""
    threads: int = 0
    # buffer: int = 0
    plot_size: int = 32
    tmp_dir: str = ""
    tmp2_dir: str = ""
    dst_dir: str = ""
    phase1_duration_raw: float = 0
    phase2_duration_raw: float = 0
    phase3_duration_raw: float = 0
    phase4_duration_raw: float = 0
    total_time_raw: float = 0
    # copy_time_raw: float = 0
    filename: str = ""
    plot_name: str = ""
    compression_level: int = 1

    def common(self) -> plotman.plotters.CommonInfo:
        return plotman.plotters.CommonInfo(
            type="bladebit",
            dstdir=self.dst_dir,
            phase=self.phase,
            tmpdir=self.tmp_dir,
            tmp2dir=self.tmp2_dir,
            started_at=self.started_at,
            plot_id=self.plot_id,
            plot_size=self.plot_size,
            buckets=0,
            threads=self.threads,
            phase1_duration_raw=self.phase1_duration_raw,
            phase2_duration_raw=self.phase2_duration_raw,
            phase3_duration_raw=self.phase3_duration_raw,
            phase4_duration_raw=self.phase4_duration_raw,
            total_time_raw=self.total_time_raw,
            filename=self.filename,
            compression_level=self.compression_level,
        )


@plotman.plotters.check_Plotter
@attr.mutable
class Plotter:
    decoder: plotman.plotters.LineDecoder = attr.ib(
        factory=plotman.plotters.LineDecoder
    )
    info: SpecificInfo = attr.ib(factory=SpecificInfo)
    parsed_command_line: typing.Optional[
        plotman.job.ParsedChiaPlotsCreateCommand
    ] = None

    @classmethod
    def identify_log(cls, line: str) -> bool:
        return "Warm start enabled" in line

    @classmethod
    def identify_process(cls, command_line: typing.List[str]) -> bool:
        if len(command_line) == 0:
            return False

        return os.path.basename(command_line[0]).lower() in {
            "bladebit",
            "bladebit_cuda",
        }

    def common_info(self) -> plotman.plotters.CommonInfo:
        return self.info.common()

    def parse_command_line(self, command_line: typing.List[str], cwd: str) -> None:
        # drop the bladebit or bladebit_cuda
        arguments = command_line[1:]

        # DEBUG ONLY: Pretend I have 512 GB RAM and could ramplot. :)
        #arguments = ['-v', '-n', '1', '-t', '14', '-f', 'abcdefg', '-c', 'xch123456', '--no-numa', 'ramplot', '/plots1']

        # TODO: We could at some point do version detection and pick the
        #       associated command.  For now we'll just use the latest one we have
        #       copied.
        command = commands.latest_command()

        self.parsed_command_line = plotman.plotters.parse_command_line_with_click(
            command=command,
            arguments=arguments,
        )

        for key in ["out_dir"]:
            original: os.PathLike[str] = self.parsed_command_line.parameters.get(key)  # type: ignore[assignment]
            if original is not None:
                self.parsed_command_line.parameters[key] = pathlib.Path(cwd).joinpath(
                    original
                )

    def update(self, chunk: bytes) -> SpecificInfo:
        new_lines = self.decoder.update(chunk=chunk)

        for line in new_lines:
            if not self.info.phase.known:
                self.info = attr.evolve(
                    self.info, phase=plotman.job.Phase(major=0, minor=0)
                )

            for pattern, handler_functions in handlers.mapping.items():
                match = pattern.search(line)

                if match is None:
                    continue

                for handler_function in handler_functions:
                    self.info = handler_function(match=match, info=self.info)

                break

        return self.info


handlers = plotman.plotters.RegexLineHandlers[SpecificInfo]()


@handlers.register(expression=r"^Running Phase (?P<phase>\d+)")
def running_phase(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    # Running Phase 1
    major = int(match.group("phase"))
    return attr.evolve(info, phase=plotman.job.Phase(major=major, minor=0))


@handlers.register(
    expression=r"^Finished Phase (?P<phase>\d+) in (?P<duration>[^ ]+) seconds."
)
def phase_finished(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    # Finished Phase 1 in 313.98 seconds.
    major = int(match.group("phase"))
    duration = float(match.group("duration"))
    duration_dict = {f"phase{major}_duration_raw": duration}
    return attr.evolve(
        info, phase=plotman.job.Phase(major=major + 1, minor=0), **duration_dict
    )

@handlers.register(expression=r"^Table (?P<table>\d+)$")
def table_started(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    table = int(match.group("table"))
    return attr.evolve(info, phase=plotman.job.Phase(major=1, minor=table))

@handlers.register(expression=r"^Allocating buffers\.$")
def allocating_buffers(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    # Allocating buffers.
    return attr.evolve(info, phase=plotman.job.Phase(major=0, minor=1))


@handlers.register(expression=r"^Finished F1 generation in")
def finished_f1(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    # Finished F1 generation in 6.93 seconds.
    return attr.evolve(info, phase=plotman.job.Phase(major=1, minor=1))


@handlers.register(expression=r"^Forward propagating to table (?P<table>\d+)")
def forward_propagating(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    # Forward propagating to table 2...
    minor = int(match.group("table"))
    return attr.evolve(info, phase=plotman.job.Phase(major=1, minor=minor))


@handlers.register(expression=r"^ *Prunn?ing table (?P<table>\d+)")
def pruning_table(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    #   Prunning table 6...
    table = int(match.group("table"))
    minor = 7 - table
    return attr.evolve(info, phase=plotman.job.Phase(major=2, minor=minor))


@handlers.register(expression=r"^ *Compressing tables (?P<table>\d+)")
def compressing_tables(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    #   Compressing tables 1 and 2...
    minor = int(match.group("table"))
    return attr.evolve(info, phase=plotman.job.Phase(major=3, minor=minor))


@handlers.register(expression=r"^ *Writing (?P<tag>(P7|C1|C2|C3))")
def phase_4_writing(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    #   Writing P7.
    minors = {"P7": 1, "C1": 2, "C2": 3, "C3": 4}
    tag = match.group("tag")
    minor = minors[tag]
    return attr.evolve(info, phase=plotman.job.Phase(major=4, minor=minor))


@handlers.register(expression=r"^Generating plot .*: (?P<plot_id>[^ ]+)")
def generating_plot(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    # Generating plot 1 / 1: 1fc7b57baae24da78e3bea44d58ab51f162a3ed4d242bab2fbcc24f6577d88b3
    return attr.evolve(
        info,
        phase=plotman.job.Phase(major=0, minor=2),
        plot_id=match.group("plot_id"),
    )


@handlers.register(expression=r"^Writing final plot tables to disk$")
def writing_final(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    # Bladebit 1.0: Writing final plot tables to disk
    return attr.evolve(info, phase=plotman.job.Phase(major=5, minor=1))


@handlers.register(expression=r"^Renaming plot to.*")
def writing_final(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    # Bladebit 2.0: Renaming *.plot.tmp to *.plot
    return attr.evolve(info, phase=plotman.job.Phase(major=5, minor=1))


@handlers.register(expression=r"^Finished plotting in (?P<duration>[^ ]+) seconds")
def total_duration(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    # Finished plotting in 582.91 seconds (9.72 minutes).
    duration = float(match.group("duration"))
    return attr.evolve(info, total_time_raw=duration)


@handlers.register(expression=r"^ *Output path *: *(.+)")
def dst_dir(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    #  Output path           : /mnt/tmp/01/manual-transfer/
    return attr.evolve(info, dst_dir=match.group(1))

@handlers.register(expression=r"^ Temp1 path\s*:\s*(.+)")
def tmp_dir(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    #  Temp1 path     : /plotting
    return attr.evolve(info, tmp_dir=match.group(1))

@handlers.register(expression=r"^ Temp2 path\s*:\s*(.+)")
def tmp2_dir(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    #  Temp2 path     : /plotting
    return attr.evolve(info, tmp2_dir=match.group(1))

@handlers.register(
    expression=r"^Plot .*/(?P<filename>(?P<name>plot-k(?P<size>\d+)(-c(?P<lvl>\d+))?-(?P<year>\d+)-(?P<month>\d+)-(?P<day>\d+)-(?P<hour>\d+)-(?P<minute>\d+)-(?P<plot_id>\w+)).plot) .*"
)
def plot_name_line(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    # Plot /mnt/tmp/01/manual-transfer/plot-k32-2021-08-29-22-22-1fc7b57baae24da78e3bea44d58ab51f162a3ed4d242bab2fbcc24f6577d88b3.plot finished writing to disk:
    try:
        compression_lvl = int(match.group("lvl"))
    except:
        compression_lvl = 0  
    return attr.evolve(
        info,
        plot_size=int(match.group("size")),
        plot_name=match.group("name"),
        started_at=pendulum.datetime(
            year=int(match.group("year")),
            month=int(match.group("month")),
            day=int(match.group("day")),
            hour=int(match.group("hour")),
            minute=int(match.group("minute")),
            tz=None,
        ),
        filename=match.group("filename"),
        plot_id=match.group("plot_id"),
        compression_level = compression_lvl,
    )


@handlers.register(expression=r"^ *Thread count *: *(\d+)")
def threads(match: typing.Match[str], info: SpecificInfo) -> SpecificInfo:
    #  Thread count          : 88
    return attr.evolve(info, threads=int(match.group(1)))


commands = plotman.plotters.core.Commands()


# BladeBit Git on 2021-08-29 -> https://github.com/harold-b/bladebit/commit/f3fbfff43ce493ec9e02db6f72c3b44f656ef137
@commands.register(version=(0,))
@click.command()
# https://github.com/harold-b/bladebit/blob/f3fbfff43ce493ec9e02db6f72c3b44f656ef137/LICENSE
# https://github.com/harold-b/bladebit/blob/f7cf06fa685c9b1811465ecd47129402bb7548a0/src/main.cpp#L75-L108
@click.option(
    "-t",
    "--threads",
    help=(
        "Maximum number of threads to use."
        "  For best performance, use all available threads (default behavior)."
        "  Values below 2 are not recommended."
    ),
    type=int,
    show_default=True,
)
@click.option(
    "-n",
    "--count",
    help="Number of plots to create. Default = 1.",
    type=int,
    default=1,
    show_default=True,
)
@click.option(
    "-f",
    "--farmer-key",
    help="Farmer public key, specified in hexadecimal format.",
    type=str,
)
@click.option(
    "-p",
    "--pool-key",
    help=(
        "Pool public key, specified in hexadecimal format."
        "  Either a pool public key or a pool contract address must be specified."
    ),
    type=str,
)
@click.option(
    "-c",
    "--pool-contract",
    help=(
        "Pool contract address, specified in hexadecimal format."
        "  Address where the pool reward will be sent to."
        "  Only used if pool public key is not specified."
    ),
    type=str,
)
@click.option(
    "-w",
    "--warm-start",
    help="Touch all pages of buffer allocations before starting to plot.",
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "-i",
    "--plot-id",
    help="Specify a plot id for debugging.",
    type=str,
)
@click.option(
    "-v",
    "--verbose",
    help="Enable verbose output.",
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "-m",
    "--no-numa",
    help=(
        "Disable automatic NUMA aware memory binding."
        "  If you set this parameter in a NUMA system you will likely get degraded performance."
    ),
    is_flag=True,
    type=bool,
    default=False,
)
@click.argument(
    "out_dir",
    # help=(
    #     "Output directory in which to output the plots." "  This directory must exist."
    # ),
    type=click.Path(),
    default=pathlib.Path("."),
    # show_default=True,
)
def _cli_f3fbfff43ce493ec9e02db6f72c3b44f656ef137() -> None:
    pass


# BladeBit Git on 2021-08-29 -> https://github.com/harold-b/bladebit/commit/b48f262336362acd6f23c5ca9a43cfd6d244cb88
@commands.register(version=(1, 1, 0))
@click.command()
# https://github.com/harold-b/bladebit/blob/b48f262336362acd6f23c5ca9a43cfd6d244cb88/LICENSE
# https://github.com/harold-b/bladebit/blob/b48f262336362acd6f23c5ca9a43cfd6d244cb88/src/main.cpp#L77-L119
@click.option(
    "-t",
    "--threads",
    help=(
        "Maximum number of threads to use."
        "  For best performance, use all available threads (default behavior)."
        "  Values below 2 are not recommended."
    ),
    type=int,
    show_default=True,
)
@click.option(
    "-n",
    "--count",
    help="Number of plots to create. Default = 1.",
    type=int,
    default=1,
    show_default=True,
)
@click.option(
    "-f",
    "--farmer-key",
    help="Farmer public key, specified in hexadecimal format.",
    type=str,
)
@click.option(
    "-p",
    "--pool-key",
    help=(
        "Pool public key, specified in hexadecimal format."
        "  Either a pool public key or a pool contract address must be specified."
    ),
    type=str,
)
@click.option(
    "-c",
    "--pool-contract",
    help=(
        "Pool contract address, specified in hexadecimal format."
        "  Address where the pool reward will be sent to."
        "  Only used if pool public key is not specified."
    ),
    type=str,
)
@click.option(
    "-w",
    "--warm-start",
    help="Touch all pages of buffer allocations before starting to plot.",
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "-i",
    "--plot-id",
    help="Specify a plot id for debugging.",
    type=str,
)
@click.option(
    "--memo",
    help="Specify a plot memo for debugging.",
    type=str,
)
@click.option(
    "--show-memo",
    help="Output the memo of the next plot the be plotted.",
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "-v",
    "--verbose",
    help="Enable verbose output.",
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "-m",
    "--no-numa",
    help=(
        "Disable automatic NUMA aware memory binding."
        "  If you set this parameter in a NUMA system you will likely get degraded performance."
    ),
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "--no-cpu-affinity",
    help=(
        "Disable assigning automatic thread affinity."
        "  This is useful when running multiple simultaneous instances of bladebit as you can manually assign thread affinity yourself when launching bladebit."
    ),
    is_flag=True,
    type=bool,
    default=False,
)
@click.argument(
    "out_dir",
    # help=(
    #     "Output directory in which to output the plots." "  This directory must exist."
    # ),
    type=click.Path(),
    default=pathlib.Path("."),
    # show_default=True,
)
def _cli_b48f262336362acd6f23c5ca9a43cfd6d244cb88() -> None:
    pass

# BladeBit Git on 2022-10-21 -> https://github.com/Chia-Network/bladebit/commit/a395f44cab55524a757a5cdb30dad4d08ee307f4
@commands.register(version=(2, 0, 0))
@click.command()
@click.option(
    "-t",
    "--threads",
    help=(
        "Maximum number of threads to use."
        "  For best performance, use all available threads (default behavior)."
        "  Values below 2 are not recommended."
    ),
    type=int,
    show_default=True,
)
@click.option(
    "-n",
    "--count",
    help="Number of plots to create. Default = 1.",
    type=int,
    default=1,
    show_default=True,
)
@click.option(
    "-f",
    "--farmer-key",
    help="Farmer public key, specified in hexadecimal format.",
    type=str,
)
@click.option(
    "-p",
    "--pool-key",
    help=(
        "Pool public key, specified in hexadecimal format."
        "  Either a pool public key or a pool contract address must be specified."
    ),
    type=str,
)
@click.option(
    "-c",
    "--pool-contract",
    help=(
        "Pool contract address, specified in hexadecimal format."
        "  Address where the pool reward will be sent to."
        "  Only used if pool public key is not specified."
    ),
    type=str,
)
@click.option(
    "-w",
    "--warm-start",
    help="Touch all pages of buffer allocations before starting to plot.",
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "-i",
    "--plot-id",
    help="Specify a plot id for debugging.",
    type=str,
)
@click.option(
    "--memo",
    help="Specify a plot memo for debugging.",
    type=str,
)
@click.option(
    "--show-memo",
    help="Output the memo of the next plot the be plotted.",
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "-v",
    "--verbose",
    help="Enable verbose output.",
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "-m",
    "--no-numa",
    help=(
        "Disable automatic NUMA aware memory binding."
        "  If you set this parameter in a NUMA system you will likely get degraded performance."
    ),
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "--no-cpu-affinity",
    help=(
        "Disable assigning automatic thread affinity."
        "  This is useful when running multiple simultaneous instances of bladebit as you can manually assign thread affinity yourself when launching bladebit."
    ),
    is_flag=True,
    type=bool,
    default=False,
)
@click.argument(
    "diskplot",
)
@click.option(
    "--cache",
    help="Size of cache to reserve for I/O.",
    type=str,
)
@click.argument(
    "out_dir",
    # help=(
    #     "Output directory in which to output the plots." "  This directory must exist."
    # ),
    type=click.Path(),
    default=pathlib.Path("."),
    # show_default=True,
)
def _cli_a395f44cab55524a757a5cdb30dad4d08ee307f4() -> None:
    pass

# BladeBit Git on 2023-01-09 -> https://github.com/Chia-Network/bladebit/commit/9fac46aff0476e829d476412de18497a3a2f7ed8
@commands.register(version=(2, 0, 1))
@click.command(context_settings=dict(allow_extra_args=True,))
@click.option(
    "-t",
    "--threads",
    help=(
        "Maximum number of threads to use."
        "  For best performance, use all available threads (default behavior)."
        "  Values below 2 are not recommended."
    ),
    type=int,
    show_default=True,
)
@click.option(
    "-n",
    "--count",
    help="Number of plots to create. Default = 1.",
    type=int,
    default=1,
    show_default=True,
)
@click.option(
    "-f",
    "--farmer-key",
    help="Farmer public key, specified in hexadecimal format.",
    type=str,
)
@click.option(
    "-p",
    "--pool-key",
    help=(
        "Pool public key, specified in hexadecimal format."
        "  Either a pool public key or a pool contract address must be specified."
    ),
    type=str,
)
@click.option(
    "-c",
    "--pool-contract",
    help=(
        "Pool contract address, specified in hexadecimal format."
        "  Address where the pool reward will be sent to."
        "  Only used if pool public key is not specified."
    ),
    type=str,
)
@click.option(
    "-w",
    "--warm-start",
    help="Touch all pages of buffer allocations before starting to plot.",
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "-i",
    "--plot-id",
    help="Specify a plot id for debugging.",
    type=str,
)
@click.option(
    "--memo",
    help="Specify a plot memo for debugging.",
    type=str,
)
@click.option(
    "--show-memo",
    help="Output the memo of the next plot the be plotted.",
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "-v",
    "--verbose",
    help="Enable verbose output.",
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "-m",
    "--no-numa",
    help=(
        "Disable automatic NUMA aware memory binding."
        "  If you set this parameter in a NUMA system you will likely get degraded performance."
    ),
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "--no-cpu-affinity",
    help=(
        "Disable assigning automatic thread affinity."
        "  This is useful when running multiple simultaneous instances of bladebit as you can manually assign thread affinity yourself when launching bladebit."
    ),
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "--cache",
    help="Size of cache to reserve for I/O.",
    type=str,
)
@click.option(
    "--diskplot",
    "diskplot",
    help="Create a plot by making use of a disk.",
    type=str,
    is_flag=True,
    default=False,
)
@click.option(
    "-b",
    "--buckets",
    help="The number of buckets to use. The default is 256.",
    type=int,
)
@click.option(
    "--f1-threads",
    help="Override the thread count for F1 generation.",
    type=int,
)
@click.option(
    "--fp-threads",
    help="Override the thread count for forward propogation.",
    type=int,
)
@click.option(
    "--c-threads",
    help="Override the thread count for C table processing.",
    type=int,
)
@click.option(
    "--p2-threads",
    help="Override the thread count for Phase 2.",
    type=int,
)
@click.option(
    "--p3-threads",
    help="Override the thread count for Phase 3.",
    type=int,
)
@click.option(
    "--ramplot",
    "ramplot",
    help="Create a plot completely in-ram.",
    type=str,
    is_flag=True,
    default=False,
)
@click.argument(
    "out_dir",
    # help=(
    #     "Output directory in which to output the plots." "  This directory must exist."
    # ),
    type=click.Path(),
    default=pathlib.Path("."),
    # show_default=True,
)
def _cli_9fac46aff0476e829d476412de18497a3a2f7ed8() -> None:
    pass

# BladeBit Git on 2023-02-10 -> https://github.com/Chia-Network/bladebit/commit/a85283946c56b5ae1e5b673f62143417db96247b
@commands.register(version=(3, 0, 0))
@click.command(context_settings=dict(allow_extra_args=True,))
@click.option(
    "-t",
    "--threads",
    help=(
        "Maximum number of threads to use."
        "  For best performance, use all available threads (default behavior)."
        "  Values below 2 are not recommended."
    ),
    type=int,
    show_default=True,
)
@click.option(
    "-n",
    "--count",
    help="Number of plots to create. Default = 1.",
    type=int,
    default=1,
    show_default=True,
)
@click.option(
    "-f",
    "--farmer-key",
    help="Farmer public key, specified in hexadecimal format.",
    type=str,
)
@click.option(
    "-p",
    "--pool-key",
    help=(
        "Pool public key, specified in hexadecimal format."
        "  Either a pool public key or a pool contract address must be specified."
    ),
    type=str,
)
@click.option(
    "-c",
    "--pool-contract",
    help=(
        "Pool contract address, specified in hexadecimal format."
        "  Address where the pool reward will be sent to."
        "  Only used if pool public key is not specified."
    ),
    type=str,
)
@click.option(
    "-w",
    "--warm-start",
    help="Touch all pages of buffer allocations before starting to plot.",
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "-i",
    "--plot-id",
    help="Specify a plot id for debugging.",
    type=str,
)
@click.option(
    "--memo",
    help="Specify a plot memo for debugging.",
    type=str,
)
@click.option(
    "--show-memo",
    help="Output the memo of the next plot the be plotted.",
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "-v",
    "--verbose",
    help="Enable verbose output.",
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "-m",
    "--no-numa",
    help=(
        "Disable automatic NUMA aware memory binding."
        "  If you set this parameter in a NUMA system you will likely get degraded performance."
    ),
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "--no-cpu-affinity",
    help=(
        "Disable assigning automatic thread affinity."
        "  This is useful when running multiple simultaneous instances of bladebit as you can manually assign thread affinity yourself when launching bladebit."
    ),
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "--cache",
    help="Size of cache to reserve for I/O.",
    type=str,
)
@click.option(
    "--diskplot",
    "diskplot",
    help="Create a plot by making use of a disk.",
    type=str,
    is_flag=True,
    default=False,
)
@click.option(
    "-b",
    "--buckets",
    help="The number of buckets to use. The default is 256.",
    type=int,
)
@click.option(
    "--f1-threads",
    help="Override the thread count for F1 generation.",
    type=int,
)
@click.option(
    "--fp-threads",
    help="Override the thread count for forward propogation.",
    type=int,
)
@click.option(
    "--c-threads",
    help="Override the thread count for C table processing.",
    type=int,
)
@click.option(
    "--p2-threads",
    help="Override the thread count for Phase 2.",
    type=int,
)
@click.option(
    "--p3-threads",
    help="Override the thread count for Phase 3.",
    type=int,
)
@click.option(
    "-C",
    "--compress",
    help="Compression level (default = 1, min = 1, max = 9)",
    type=int,
    default=1,
    show_default=True,
)
@click.option(
    "--ramplot",
    "ramplot",
    help="Create a plot completely in-ram.",
    type=str,
    is_flag=True,
    default=False,
)
@click.option(
    "--cudaplot",
    "cudaplot",
    help="Create a plot by using the a CUDA-capable GPU.",
    type=str,
    is_flag=True,
    default=False,
)
@click.argument(
    "out_dir",
    # help=(
    #     "Output directory in which to output the plots." "  This directory must exist."
    # ),
    type=click.Path(),
    default=pathlib.Path("."),
    # show_default=True,
)
def _cli_a85283946c56b5ae1e5b673f62143417db96247b() -> None:
    pass

# BladeBit Git on 2023-10-08 -> https://github.com/Chia-Network/bladebit/commit/e9836f8bd963321457bc86eb5d61344bfb76dcf0
@commands.register(version=(3, 1, 0))
@click.command(context_settings=dict(allow_extra_args=True,))
@click.option(
    "-t",
    "--threads",
    help=(
        "Maximum number of threads to use."
        "  For best performance, use all available threads (default behavior)."
        "  Values below 2 are not recommended."
    ),
    type=int,
    show_default=True,
)
@click.option(
    "-n",
    "--count",
    help="Number of plots to create. Default = 1.",
    type=int,
    default=1,
    show_default=True,
)
@click.option(
    "-f",
    "--farmer-key",
    help="Farmer public key, specified in hexadecimal format.",
    type=str,
)
@click.option(
    "-p",
    "--pool-key",
    help=(
        "Pool public key, specified in hexadecimal format."
        "  Either a pool public key or a pool contract address must be specified."
    ),
    type=str,
)
@click.option(
    "-c",
    "--pool-contract",
    help=(
        "Pool contract address, specified in hexadecimal format."
        "  Address where the pool reward will be sent to."
        "  Only used if pool public key is not specified."
    ),
    type=str,
)
@click.option(
    "-w",
    "--warm-start",
    help="Touch all pages of buffer allocations before starting to plot.",
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "-i",
    "--plot-id",
    help="Specify a plot id for debugging.",
    type=str,
)
@click.option(
    "--memo",
    help="Specify a plot memo for debugging.",
    type=str,
)
@click.option(
    "--show-memo",
    help="Output the memo of the next plot the be plotted.",
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "-v",
    "--verbose",
    help="Enable verbose output.",
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "-m",
    "--no-numa",
    help=(
        "Disable automatic NUMA aware memory binding."
        "  If you set this parameter in a NUMA system you will likely get degraded performance."
    ),
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "--no-direct-io",
    help=(
        "Disable direct I/O when writing plot files. Enable this if writing to a storage destination that does not support direct I/O."
    ),
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "--disk-128",
    help=(
        "Enable hybrid disk plotting for 128G system RAM."
        "  Requires a --temp1 and --temp2 to be set."
    ),
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "--disk-16",
    help=(
        "Enable hybrid disk plotting for 16G system RAM."
        "  Requires a --temp1 and --temp2 to be set."
    ),
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "--no-cpu-affinity",
    help=(
        "Disable assigning automatic thread affinity."
        "  This is useful when running multiple simultaneous instances of bladebit as you can manually assign thread affinity yourself when launching bladebit."
    ),
    is_flag=True,
    type=bool,
    default=False,
)
@click.option(
    "--cache",
    help="Size of cache to reserve for I/O.",
    type=str,
)
@click.option(
    "--diskplot",
    "diskplot",
    help="Create a plot by making use of a disk.",
    type=str,
    is_flag=True,
    default=False,
)
@click.option(
    "-b",
    "--buckets",
    help="The number of buckets to use. The default is 256.",
    type=int,
)
@click.option(
    "--f1-threads",
    help="Override the thread count for F1 generation.",
    type=int,
)
@click.option(
    "--fp-threads",
    help="Override the thread count for forward propogation.",
    type=int,
)
@click.option(
    "--c-threads",
    help="Override the thread count for C table processing.",
    type=int,
)
@click.option(
    "--p2-threads",
    help="Override the thread count for Phase 2.",
    type=int,
)
@click.option(
    "--p3-threads",
    help="Override the thread count for Phase 3.",
    type=int,
)
@click.option(
    "-C",
    "--compress",
    help="Compression level (default = 1, min = 1, max = 9)",
    type=int,
    default=1,
    show_default=True,
)
@click.option(
    "--ramplot",
    "ramplot",
    help="Create a plot completely in-ram.",
    type=str,
    is_flag=True,
    default=False,
)
@click.option(
    "--cudaplot",
    "cudaplot",
    help="Create a plot by using the a CUDA-capable GPU.",
    type=str,
    is_flag=True,
    default=False,
)
@click.option(
    "--check",
    help="UNDOCUMENTED FEATURE: On cudaplot, allow plots to be checked via the --check <n> parameter.",
    type=int,
)
@click.argument(
    "out_dir",
    # help=(
    #     "Output directory in which to output the plots." "  This directory must exist."
    # ),
    type=click.Path(),
    default=pathlib.Path("."),
    # show_default=True,
)
def _cli_e9836f8bd963321457bc86eb5d61344bfb76dcf0() -> None:
    pass
