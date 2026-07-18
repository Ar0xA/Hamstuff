#this works better

import re
import os
import csv
import itertools
from collections import defaultdict
import statistics

CLIENT_FILES = {
    "Normal": "wsjtx_norm.txt",
    "2-Pass": "wsjtx_2stage.txt",
    "3-Pass": "wsjtx_3stage.txt",
    "Late": "wsjtx_late.txt"
}

WEAK_SNR_THRESHOLD = -20

# Frequency-bucket tolerance for the join key. Needed for the same reason
# as in the cross-program script: short common messages ("73", "RR73") can
# repeat within one 15-second cycle on different frequencies, and without
# a frequency component in the key those would collide.
FREQ_BUCKET_HZ = 10

# How many 15-second cycles of shift to search, in each direction, AFTER
# each file has been normalized to its own first decode (see main()). This
# is now searching only "was this file's true cycle 0 silent" -- a few
# cycles at most -- not the raw gap between button-clicks across runs,
# which is handled by the normalization step instead. 10 cycles = 150s is
# generous headroom for that residual uncertainty.

STANDARD_PATTERN = re.compile(
    r'^(\d{6,8})_(\d{6})\s+'
    r'[\d.]+\s+'
    r'Rx\s+'
    r'\S+\s+'
    r'(-?\d+)\s+'
    r'(-?\d+\.\d+)\s+'
    r'(\d+)\s+'
    r'(.+)$',
    re.IGNORECASE
)

TX_LINE_PATTERN = re.compile(r'\bTx\b')


def time_to_bucket(time_str):
    try:
        h, m, s = int(time_str[0:2]), int(time_str[2:4]), int(time_str[4:6])
        total_seconds = h * 3600 + m * 60 + s
        return (total_seconds // 15) * 15
    except ValueError:
        return None


def freq_to_bucket(freq_hz):
    return (freq_hz // FREQ_BUCKET_HZ) * FREQ_BUCKET_HZ


def clean_message(raw_msg):
    msg = raw_msg.strip().upper()
    msg = re.sub(r'\s+(A\d?|[\^*?]+)$', '', msg)
    return msg.strip()


def parse_standard_format(filepath):
    decodes = []
    total_lines = 0
    tx_lines = 0
    unparsed_rx_lines = 0

    if not os.path.exists(filepath):
        print(f"Warning: File {filepath} not found. Skipping...")
        return decodes, total_lines, tx_lines, unparsed_rx_lines

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total_lines += 1

            match = STANDARD_PATTERN.match(line)
            if not match:
                if TX_LINE_PATTERN.search(line):
                    tx_lines += 1
                else:
                    unparsed_rx_lines += 1
                continue

            date_str, time_str, snr_str, dt_str, freq_str, raw_msg = match.groups()
            msg = clean_message(raw_msg)
            if len(msg) <= 3:
                continue

            time_bucket = time_to_bucket(time_str)
            if time_bucket is None:
                continue

            decodes.append({
                'time_bucket': time_bucket,
                'freq_bucket': freq_to_bucket(int(freq_str)),
                'snr': int(snr_str),
                'msg': msg
            })

    return decodes, total_lines, tx_lines, unparsed_rx_lines


def find_best_shift(decodes, reference_set, search_range_cycles=10):
    """
    Searches a shift in [-search_range_cycles, +search_range_cycles] cycles
    and returns whichever maximizes matches against reference_set. Expects
    `decodes` to already be normalized to elapsed seconds since THIS file's
    own first decode (see main()) -- the residual uncertainty being
    searched here is only "was cycle 0 silent for this run" (a few cycles
    at most), not the raw gap between when you clicked File->Open for each
    run (which could be minutes, varies run to run, and is NOT what we
    actually want to align on).
    """
    best_shift_seconds = 0
    best_count = -1
    zero_shift_count = None

    for shift_cycles in range(-search_range_cycles, search_range_cycles + 1):
        shift_seconds = shift_cycles * 15
        count = 0
        for d in decodes:
            key = (d['time_bucket'] - shift_seconds, d['freq_bucket'], d['msg'])
            if key in reference_set:
                count += 1
        if shift_seconds == 0:
            zero_shift_count = count
        if count > best_count:
            best_count = count
            best_shift_seconds = shift_seconds

    return best_shift_seconds, best_count, zero_shift_count


def main():
    setting_names = list(CLIENT_FILES.keys())
    reference_name = setting_names[0]

    print("Parsing files...")
    client_decodes = {}
    print("\n--- PARSE YIELD (sanity check) ---")
    for setting, filename in CLIENT_FILES.items():
        decodes, total_lines, tx_lines, unparsed_rx_lines = parse_standard_format(filename)
        client_decodes[setting] = decodes
        rx_lines = total_lines - tx_lines
        yield_pct = (len(decodes) / rx_lines * 100) if rx_lines else 0
        print(f"{setting:<8}: {len(decodes):,} parsed / {rx_lines:,} Rx lines "
              f"({yield_pct:.1f}%), {tx_lines:,} Tx lines skipped, "
              f"{unparsed_rx_lines:,} unparsed Rx-looking lines")

    print(f"\n--- ALIGNMENT (reference = {reference_name}) ---")

    # Normalize every file, INCLUDING the reference, to elapsed seconds
    # since that file's own first decode. This deliberately discards the
    # raw button-click gap between runs (unpredictable, can be minutes,
    # not the thing we want to align on) and searches only the much
    # smaller residual uncertainty of "was this file's first real cycle
    # silent."
    normalized = {}
    file_start_info = {}
    for setting in setting_names:
        decodes = client_decodes[setting]
        if not decodes:
            normalized[setting] = []
            continue
        min_bucket = min(d['time_bucket'] for d in decodes)
        max_bucket = max(d['time_bucket'] for d in decodes)
        file_start_info[setting] = (min_bucket, max_bucket)
        normalized[setting] = [
            {
                'time_bucket': d['time_bucket'] - min_bucket,
                'freq_bucket': d['freq_bucket'],
                'snr': d['snr'],
                'msg': d['msg']
            }
            for d in decodes
        ]

    for setting in setting_names:
        if setting in file_start_info:
            span_min = (file_start_info[setting][1] - file_start_info[setting][0] + 15) / 60.0
            print(f"{setting:<8}: first decode at raw {file_start_info[setting][0]}s, "
                  f"span {span_min:.1f} min")

    reference_set = {
        (d['time_bucket'], d['freq_bucket'], d['msg'])
        for d in normalized[reference_name]
    }
    aligned_decodes = {reference_name: normalized[reference_name]}
    for setting in setting_names:
        if setting == reference_name:
            print(f"{setting:<8}: reference file, shift = 0 cycles")
            continue
        shift_seconds, best_count, zero_shift_count = find_best_shift(
            normalized[setting], reference_set, search_range_cycles=10
        )
        print(f"{setting:<8}: best shift = {shift_seconds:+d}s "
              f"({best_count:,} matches vs {zero_shift_count:,} matches at shift=0)")
        if shift_seconds != 0:
            print(f"          ^ this file's own cycle 0 was likely silent -- "
                  f"corrected by {shift_seconds // 15:+d} cycle(s).")
        shifted = []
        for d in normalized[setting]:
            shifted.append({
                'time_bucket': d['time_bucket'] - shift_seconds,
                'freq_bucket': d['freq_bucket'],
                'snr': d['snr'],
                'msg': d['msg']
            })
        aligned_decodes[setting] = shifted

    master_log = defaultdict(dict)
    for setting in setting_names:
        for d in aligned_decodes[setting]:
            key = (d['time_bucket'], d['freq_bucket'], d['msg'])
            if setting in master_log[key]:
                master_log[key][setting] = max(master_log[key][setting], d['snr'])
            else:
                master_log[key][setting] = d['snr']

    if not master_log:
        print("No decodes found. Check file names and formats.")
        return

    stats = {s: {'total': 0, 'unique': 0, 'weak_count': 0, 'snr_list': [], 'missed': 0}
              for s in setting_names}
    total_unique_signals = len(master_log)
    decoded_by_all = 0
    pairwise_overlap = {pair: 0 for pair in itertools.combinations(setting_names, 2)}

    for key, decoders in master_log.items():
        decoder_list = list(decoders.keys())

        if len(decoder_list) == len(setting_names):
            decoded_by_all += 1
        if len(decoder_list) == 1:
            stats[decoder_list[0]]['unique'] += 1
        for pair in pairwise_overlap:
            if pair[0] in decoders and pair[1] in decoders:
                pairwise_overlap[pair] += 1

        for s in setting_names:
            if s in decoders:
                snr = decoders[s]
                stats[s]['total'] += 1
                stats[s]['snr_list'].append(snr)
                if snr <= WEAK_SNR_THRESHOLD:
                    stats[s]['weak_count'] += 1
            else:
                stats[s]['missed'] += 1

    print("\n" + "="*55)
    print(" WSJT-X DECODE-START SETTING COMPARISON (replayed WAV)")
    print("="*55)

    print(f"\nOverall Unique Signals Heard Across All Settings: {total_unique_signals:,}")
    print(f"Signals Decoded by ALL FOUR Settings Simultaneously: {decoded_by_all:,}")

    print("\n--- TOTAL DECODES ---")
    for s in setting_names:
        print(f"{s:<8}: {stats[s]['total']:,} decodes")

    print("\n--- UNIQUE DECODES (this setting ONLY) ---")
    for s in setting_names:
        print(f"{s:<8}: {stats[s]['unique']:,} decodes")

    print(f"\n--- WEAK SIGNAL PERFORMANCE (<= {WEAK_SNR_THRESHOLD} dB) ---")
    for s in setting_names:
        print(f"{s:<8}: {stats[s]['weak_count']:,} weak signals decoded")

    print("\n--- DECODED SNR (mean / median / stdev) ---")
    for s in setting_names:
        vals = stats[s]['snr_list']
        if vals:
            print(f"{s:<8}: mean {statistics.mean(vals):.2f} dB / "
                  f"median {statistics.median(vals):.2f} dB / "
                  f"stdev {(statistics.stdev(vals) if len(vals) > 1 else 0.0):.2f} dB")
        else:
            print(f"{s:<8}: N/A")

    print("\n--- MISSED DECODES (heard by at least one other setting) ---")
    for s in setting_names:
        print(f"{s:<8}: {stats[s]['missed']:,} signals missed")

    print("\n--- PAIRWISE OVERLAP (signals both settings decoded) ---")
    for pair, count in sorted(pairwise_overlap.items(), key=lambda x: -x[1]):
        print(f"{pair[0]:<8} & {pair[1]:<8}: {count:,}")

    print("\n" + "="*55)

    csv_filename = "decode_settings_replay_audit.csv"
    try:
        with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            headers = ["Aligned_Time_Bucket_s", "Freq_Bucket_Hz", "Message"] + \
                      [f"{s}_SNR" for s in setting_names]
            writer.writerow(headers)
            for key in sorted(master_log.keys(), key=lambda k: k[0]):
                time_bucket, freq_bucket, msg = key
                row = [time_bucket, freq_bucket, msg]
                for s in setting_names:
                    row.append(master_log[key].get(s, ""))
                writer.writerow(row)
        print(f"Audit log saved to '{csv_filename}' -- open in Excel to spot-check alignment and matches.")
    except Exception as e:
        print(f"Failed to write CSV: {e}")


if __name__ == "__main__":
    main()
