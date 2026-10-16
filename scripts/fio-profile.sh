#!/usr/bin/env bash
# File-based SSD latency profiling with an explicit device safety gate.
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Usage:
  fio-profile.sh --device /dev/DEVICE --file /mounted/path/fio-test.dat [options]

Required:
  -d, --device PATH       Block device the test file must reside on (required).
  -f, --file PATH         New test-file path on that device (required).

Options:
  -p, --profile NAME      all, baseline, block-size, mix, qd, pattern, or states (default: all)
  -s, --size SIZE         Test-file size (default: 10G; fio size syntax)
  -t, --runtime SECONDS   Regular profile runtime (default: 60 seconds)
      --state-runtime SEC Runtime for state workloads (default: 1800 seconds)
      --gc-idle SEC       Idle period between GC stress and recovery sample (default: 300)
  -o, --output DIR        Results directory (default: fio-results-TIMESTAMP)
      --buffered          Use buffered I/O; direct=1 is the default
      --dry-run           Print workload commands without creating/running tests
  -y, --yes               Do not prompt for confirmation (still checks device safety)
  -h, --help              Show this help

Results include one FIO JSON file per workload, latency logs, and summary.csv.
This script creates and writes the specified test file. It never targets the raw
device. Existing test files are refused to prevent accidental data loss.
USAGE
}

die() { printf 'Error: %s\n' "$*" >&2; exit 2; }
note() { printf '\n==> %s\n' "$*"; }

device=''
test_file=''
profile='all'
size='10G'
runtime=60
state_runtime=1800
gc_idle=300
output_dir="fio-results-$(date +%Y%m%d-%H%M%S)"
direct=1
dry_run=0
assume_yes=0

while (($#)); do
  case "$1" in
    -d|--device) (($# >= 2)) || die "$1 requires a value"; device=$2; shift 2 ;;
    -f|--file) (($# >= 2)) || die "$1 requires a value"; test_file=$2; shift 2 ;;
    -p|--profile) (($# >= 2)) || die "$1 requires a value"; profile=$2; shift 2 ;;
    -s|--size) (($# >= 2)) || die "$1 requires a value"; size=$2; shift 2 ;;
    -t|--runtime) (($# >= 2)) || die "$1 requires a value"; runtime=$2; shift 2 ;;
    --state-runtime) (($# >= 2)) || die "$1 requires a value"; state_runtime=$2; shift 2 ;;
    --gc-idle) (($# >= 2)) || die "$1 requires a value"; gc_idle=$2; shift 2 ;;
    -o|--output) (($# >= 2)) || die "$1 requires a value"; output_dir=$2; shift 2 ;;
    --buffered) direct=0; shift ;;
    --dry-run) dry_run=1; shift ;;
    -y|--yes) assume_yes=1; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; (($# == 0)) || die "unexpected positional arguments" ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ -n $device ]] || die 'specify --device; refusing to guess a drive'
[[ -n $test_file ]] || die 'specify --file with a new file path on the selected drive'
[[ $runtime =~ ^[1-9][0-9]*$ && $state_runtime =~ ^[1-9][0-9]*$ && $gc_idle =~ ^[0-9]+$ ]] || die 'runtime values must be positive integer seconds (gc-idle may be zero)'
[[ $size =~ ^[1-9][0-9]*([KMGTP]i?B?)?$ ]] || die "unsupported --size value: $size"
case "$profile" in all|baseline|block-size|mix|qd|pattern|states) ;; *) die "unknown profile: $profile" ;; esac

for bin in lsblk findmnt realpath python3; do command -v "$bin" >/dev/null || die "required command not found: $bin"; done
(( dry_run )) || command -v fio >/dev/null || die 'required command not found: fio'
[[ -b $device ]] || die "--device must be a block device: $device"
device=$(realpath -- "$device")
[[ $test_file = /* ]] || die '--file must be an absolute path'
test_file=$(realpath -m -- "$test_file")
[[ ! -e $test_file ]] || die "test file already exists (refusing to overwrite): $test_file"
parent=$(dirname -- "$test_file")
[[ -d $parent ]] || die "parent directory does not exist: $parent"
[[ ! -L $parent ]] || die 'test file parent directory may not be a symlink'

mount_source=$(findmnt -n -o SOURCE -T "$parent")
[[ -n $mount_source ]] || die "cannot determine backing device for $parent"
mount_source=${mount_source%%\[*}
[[ -b $mount_source ]] || die "filesystem source is not a directly identifiable block device ($mount_source); use a simple mounted block-device filesystem"
mount_source=$(realpath -- "$mount_source")

device_matches_source() {
  local selected=$1 source=$2 node
  while IFS= read -r node; do
    [[ $(realpath -- "$node") == "$selected" ]] && return 0
  done < <(lsblk -s -n -r -p -o NAME "$source")
  return 1
}
device_matches_source "$device" "$mount_source" || die "$test_file is on $mount_source, not on selected device $device"

# Do not allow the selected device to be or contain the filesystem backing /, /boot, or /boot/efi.
for critical_mount in / /boot /boot/efi; do
  [[ -e $critical_mount ]] || continue
  critical_source=$(findmnt -n -o SOURCE -T "$critical_mount")
  critical_source=${critical_source%%\[*}
  [[ -b $critical_source ]] || continue
  critical_source=$(realpath -- "$critical_source")
  if device_matches_source "$device" "$critical_source"; then
    die "selected device $device contains the system filesystem mounted at $critical_mount"
  fi
done

if (( ! dry_run )); then
  mkdir -p -- "$output_dir"
fi

if (( ! assume_yes && ! dry_run )); then
  printf 'About to create and write a %s test file at:\n  %s\nDevice: %s\nProfile: %s\nDirect I/O: %s\n' \
    "$size" "$test_file" "$device" "$profile" "$([[ $direct == 1 ]] && printf enabled || printf disabled)"
  read -r -p 'Continue? Type YES to proceed: ' response
  [[ $response == YES ]] || die 'cancelled'
fi

write_args=(--name=seed --filename="$test_file" --size="$size" --rw=write --bs=1M --ioengine=libaio --iodepth=16 --numjobs=1 --direct="$direct" --refill_buffers=1 --buffer_compress_percentage=0 --end_fsync=1 --output-format=json)

run_job() {
  local label=$1 rw=$2 bs=$3 depth=$4 seconds=$5 extra=${6:-}
  local json="$output_dir/${label}.json"
  local logbase="$output_dir/${label}-lat"
  local args=(--name="$label" --filename="$test_file" --size="$size" --ioengine=libaio --direct="$direct" --time_based=1 --runtime="$seconds" --ramp_time=5 --rw="$rw" --bs="$bs" --iodepth="$depth" --numjobs=1 --group_reporting=1 --lat_percentiles=1 --clat_percentiles=1 --percentile_list=95:99:99.9 --write_lat_log="$logbase" --log_avg_msec=1000 --output-format=json --output="$json")
  if [[ $label == pattern_* ]]; then args+=(--refill_buffers=1); fi
  [[ -z $extra ]] || args+=("$extra")
  note "$label (rw=$rw bs=$bs qd=$depth runtime=${seconds}s direct=$direct)"
  if (( dry_run )); then printf 'fio'; printf ' %q' "${args[@]}"; printf '\n'; return; fi
  if [[ $label == state_temperature_sustained_write ]]; then
    (
      while :; do
        printf '\n--- %s ---\n' "$(date --iso-8601=seconds)"
        if command -v nvme >/dev/null && [[ $(basename -- "$device") == nvme* ]]; then
          nvme smart-log "$device" 2>&1 || true
        elif command -v smartctl >/dev/null; then
          smartctl -A "$device" 2>&1 || true
        fi
        sleep 10
      done
    ) >> "$output_dir/temperature-samples.log" 2>&1 &
    local monitor_pid=$!
    if ! fio "${args[@]}" >/dev/null; then kill "$monitor_pid" 2>/dev/null || true; wait "$monitor_pid" 2>/dev/null || true; return 1; fi
    kill "$monitor_pid" 2>/dev/null || true
    wait "$monitor_pid" 2>/dev/null || true
  else
    fio "${args[@]}" >/dev/null
  fi
  printf '%s,%s,%s,%s,%s,%s\n' "$label" "$rw" "$bs" "$depth" "$seconds" "$(basename -- "$json")" >> "$output_dir/workloads.csv"
}

run_profile() {
  local selected=$1 bs rw n
  case "$selected" in
    baseline)
      run_job baseline_randread_4k randread 4k 1 "$runtime"
      run_job baseline_randwrite_4k randwrite 4k 1 "$runtime"
      run_job baseline_seqread_128k read 128k 1 "$runtime"
      run_job baseline_seqwrite_128k write 128k 1 "$runtime" ;;
    block-size)
      for bs in 4k 16k 32k 64k 128k; do
        for rw in randread randwrite read write; do run_job "block_${bs}_${rw}" "$rw" "$bs" 1 "$runtime"; done
      done
      for rw in randread randwrite read write; do run_job "block_256k_${rw}" "$rw" 256k 1 "$runtime"; done
      for rw in read write; do run_job "block_512k_${rw}" "$rw" 512k 1 "$runtime"; done ;;
    mix)
      run_job mix_read100 randread 4k 1 "$runtime"
      run_job mix_write100 randwrite 4k 1 "$runtime"
      run_job mix_read50_randrw randrw 4k 1 "$runtime" --rwmixread=50
      run_job mix_read70_randrw randrw 4k 1 "$runtime" --rwmixread=70 ;;
    qd)
      for n in 1 2 4 8 16 32 64 128 256; do
        run_job "qd_${n}_randread_4k" randread 4k "$n" "$runtime"
        run_job "qd_${n}_randwrite_4k" randwrite 4k "$n" "$runtime"
      done ;;
    pattern)
      run_job pattern_incompressible_randwrite randwrite 128k 1 "$runtime" '--buffer_compress_percentage=0'
      run_job pattern_compressible_randwrite randwrite 128k 1 "$runtime" '--buffer_compress_percentage=90' ;;
    states)
      run_states ;;
    all)
      run_profile baseline
      run_profile block-size
      run_profile mix
      run_profile qd
      run_profile pattern
      run_states ;;
  esac
}

run_states() {
  local device_name smartlog="$output_dir/device-smart-before.txt"
  device_name=$(basename -- "$device")
  if (( ! dry_run )) && command -v nvme >/dev/null && [[ $device_name == nvme* ]]; then
    nvme smart-log "$device" > "$smartlog" 2>&1 || true
  elif (( ! dry_run )) && command -v smartctl >/dev/null; then
    smartctl -a "$device" > "$smartlog" 2>&1 || true
  fi
  # The first test-file pass is the practical fresh-state sample; "fresh" cannot
  # imply factory-fresh or erased NAND, which requires device-specific preparation.
  if (( ! fresh_already )); then run_job state_fresh_first_write write 128k 1 "$runtime"; fi
  # Sustained overwrites on the fixed test file, with time-series latency/bandwidth logs.
  run_job state_steady_sustained_randwrite randwrite 128k 32 "$state_runtime"
  run_job state_temperature_sustained_write write 128k 16 "$state_runtime"
  run_job state_slc_exhaustion_write write 1m 16 "$state_runtime"
  run_job state_gc_stress_randwrite randwrite 4k 32 "$state_runtime"
  if (( gc_idle > 0 && ! dry_run )); then note "GC recovery idle period (${gc_idle}s)"; sleep "$gc_idle"; fi
  run_job state_gc_recovery_randwrite randwrite 4k 32 "$runtime"
  if (( ! dry_run )) && [[ -f $smartlog ]]; then
    if command -v nvme >/dev/null && [[ $device_name == nvme* ]]; then nvme smart-log "$device" > "$output_dir/device-smart-after.txt" 2>&1 || true
    elif command -v smartctl >/dev/null; then smartctl -a "$device" > "$output_dir/device-smart-after.txt" 2>&1 || true; fi
  fi
}

if (( ! dry_run )); then
  printf 'label,rw,block_size,queue_depth,runtime_seconds,json_file\n' > "$output_dir/workloads.csv"
fi

fresh_already=0
if [[ $profile == all || $profile == states ]]; then
  # Capture the first write to the newly named test file before any preconditioning.
  run_job state_fresh_first_write write 128k 1 "$runtime"
  fresh_already=1
fi

if [[ $profile != states ]]; then
  if (( ! dry_run )); then
    note "Preparing test-file contents for read profiles"
    fio "${write_args[@]}" --output="$output_dir/initialization.json" >/dev/null
  else
    printf 'fio'; printf ' %q' "${write_args[@]}"; printf '\n'
  fi
fi
run_profile "$profile"

if (( ! dry_run )); then
  python3 - "$output_dir" <<'PY'
import csv, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
with (root / "workloads.csv").open(newline="") as source, (root / "summary.csv").open("w", newline="") as output:
    rows = csv.DictReader(source)
    writer = csv.writer(output)
    writer.writerow(["label", "rw", "block_size", "queue_depth", "runtime_seconds", "read_p95_us", "read_p99_us", "read_p99_9_us", "write_p95_us", "write_p99_us", "write_p99_9_us", "json_file"])
    for row in rows:
        data = json.loads((root / row["json_file"]).read_text())
        job = data["jobs"][0]
        vals = []
        for direction in ("read", "write"):
            percentiles = job.get(direction, {}).get("clat_ns", {}).get("percentile", {})
            vals.extend([percentiles.get("95.000000"), percentiles.get("99.000000"), percentiles.get("99.900000")])
        vals = [None if value is None else float(value) / 1000 for value in vals]
        writer.writerow([row["label"], row["rw"], row["block_size"], row["queue_depth"], row["runtime_seconds"], *vals, row["json_file"]])
PY
  note "Finished. Summary: $output_dir/summary.csv"
fi
