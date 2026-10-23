# FIO SSD latency profiles

`scripts/fio-profile.sh` runs file-based FIO workloads and reports completion-latency P95, P99, and P99.9. It does not issue I/O to a raw device. It requires both the backing block device and an absolute path to a **new** test file on that device; existing files are deliberately refused.

## Project Report

The full report for the project with full results and analysis are available in `./Report`.

## Requirements and safe invocation

Install `fio`, `util-linux` (`lsblk` and `findmnt`), and Python 3. On Linux, the script verifies that the test-file filesystem is on the selected device (or one of its partitions) and refuses devices that back `/`, `/boot`, or `/boot/efi`. The output folder can be elsewhere.

```bash
chmod +x scripts/fio-profile.sh
scripts/fio-profile.sh \
  --device /dev/nvme1n1 \
  --file /mnt/benchmark/fio-profile.dat \
  --profile all
```

It prompts before the first write. Use `--yes` for unattended operation, but only after checking both paths. The specified file is initialized, then overwritten repeatedly by the profiles. It remains on disk after the run; remove it yourself when you no longer need it. The file must have at least the requested size available. Do not use a valuable file as the target.

Direct I/O is enabled by default (`direct=1`) to bypass the OS page cache. `--buffered` opts into buffered I/O. The 10 GiB file and 60-second runtime are defaults and can be changed using `--size` and `--runtime`. For example, `--size 40G --runtime 120` uses a larger working set and two minutes per regular workload. `--dry-run` prints the FIO commands without creating files or running tests.

## Profiles

`--profile` accepts `all` (default), `baseline`, `block-size`, `mix`, `qd`, `pattern`, or `states`.

- **baseline:** 4 KiB random read/write and 128 KiB sequential read/write at QD 1.
- **block-size:** 4, 16, 32, 64, 128, and 256 KiB random and sequential reads/writes; 512 KiB sequential read/write only.
- **mix:** 4 KiB random 100% read, 100% write, 50/50, and 70/30 read/write.
- **qd:** 4 KiB random read and random write at QD 1, 2, 4, 8, 16, 32, 64, 128, and 256.
- **pattern:** 128 KiB random writes with incompressible and 90%-compressible buffers. Compression behavior is device/controller-dependent; the option may not affect all drives.
- **states:** first write to the new test file, sustained random/sequential writes, an SLC-cache exhaustion-oriented write, and a random-write stress/idle/recovery sequence. These use `--state-runtime` (default 1800 seconds); `--gc-idle` controls the idle interval (default 300 seconds).

State names describe workload phases, not guaranteed physical states. A file-based test cannot make NAND factory-fresh; true fresh-state testing requires device-specific secure erase/reset and may require unmounting the drive. Steady state depends on drive capacity, overprovisioning, workload, and prior history. SLC exhaustion requires writing beyond the drive's available cache; a 10 GiB file may be insufficient, so increase `--size` and/or `--state-runtime` for larger devices. The fixed-file workload repeatedly overwrites the same file and cannot guarantee complete-device preconditioning.

The state jobs write time-series latency/bandwidth logs at 1-second intervals. SMART/NVMe health snapshots are collected before and after state workloads when `nvme` or `smartctl` is available; the temperature phase samples SMART data every 10 seconds when either tool is present. Temperature and GC behavior are drive-specific; compare these logs and health data rather than assuming a timer proves a state transition. For rigorous temperature profiling, also record ambient temperature.

## Output interpretation

The result directory contains a FIO JSON document for each job, latency log files, `workloads.csv`, and `summary.csv`. The summary contains read/write completion latency percentiles in microseconds; the direction not used by a job is blank. FIO JSON output is retained so the histogram and other metrics can be analyzed independently. For QD 256 and the longer state profiles, review the runtime and system load before running the complete `all` suite: its duration is the sum of all workload runtimes plus initialization and the GC idle interval.
