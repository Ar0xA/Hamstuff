#compare WSJT-X decoder settings (Normal / 2-Pass / 3-Pass / Late)
#
# IMPORTANT, READ BEFORE MODIFYING: these four files are SERIAL recordings
# -- four separate ~30-minute real-world sessions, one per setting, run one
# after another (not simultaneously, not from an identical replayed audio
# file). That means the same real signal cannot appear in more than one
# file: each session's RF content is unique to that window in time.
#
# Because of that, per-signal matching (joining on time+frequency+message
# to find "decoded by all" / "unique" / "missed" signals) is NOT valid for
# this data -- it will always show ~100% unique / ~0% overlap no matter how
# correct the parser is, because there is no shared signal to match. That
# section has been deliberately removed rather than patched, since a
# "cleaner-looking" number here would just be a more convincing wrong
# answer. If you switch to a genuinely parallel capture (same audio fed to
# all four instances at once, or one recording replayed identically into
# each) THAT data would support per-signal matching again -- see the
# separate compare_decodes.py / earlier per-signal-join version for that
# case, don't bolt matching back onto this file for serial data.
#
# What IS valid across separate sessions: duration-normalized decode RATE
# and distribution-shape comparisons (mean/median/stdev SNR, weak-signal
# SHARE of that session's own total). Even these carry one irreducible
# caveat: differences between settings could still be band-condition drift
# between sessions, not the setting itself. No code removes that; only
# collecting the data differently (parallel/replay) would.

import re
import os
from collections import defaultdict
import statistics

CLIENT_FILES = {
    "Normal": "wsjtx_norm.txt",
    "2-Pass": "wsjtx_2stage.txt",
    "3-Pass": "wsjtx_3stage.txt",
    "Late": "wsjtx_late.txt"
}

WEAK_SNR_THRESHOLD = -20

# WSJT-X fixed-column log layout, confirmed from real sample lines earlier
# in this session. Date group widened to 6-8 digits (YYMMDD or YYYYMMDD)
# for robustness; day-of-month is always the last two digits either way.
STANDARD_PATTERN = re.compile(
    r'^(\d{6,8})_(\d{6})\s+'    # group 1: date, group 2: time HHMMSS
    r'[\d.]+\s+'                 # dial frequency (MHz) - not needed
    r'Rx\s+'                     # direction marker (Rx lines only)
    r'\S+\s+'                    # mode (FT8, FT4, etc.) - not needed
    r'(-?\d+)\s+'                 # group 3: SNR
    r'(-?\d+\.\d+)\s+'            # group 4: DT (unused, kept for completeness)
    r'(\d+)\s+'                   # group 5: audio frequency (Hz) - unused here, no join key needed
    r'(.+)$',                     # group 6: decoded message
    re.IGNORECASE
)

TX_LINE_PATTERN = re.compile(r'\bTx\b')


def time_to_bucket(time_str):
    """Converts HHMMSS to a 15-second-cycle bucket, as total elapsed seconds
    since midnight. Used here only to measure session span, not to match
    across files (see module docstring)."""
    try:
        h, m, s = int(time_str[0:2]), int(time_str[2:4]), int(time_str[4:6])
        total_seconds = h * 3600 + m * 60 + s
        return (total_seconds // 15) * 15
    except ValueError:
        return None


def clean_message(raw_msg):
    """Strips trailing decode-quality markers seen on WSJT-X-family output."""
    msg = raw_msg.strip().upper()
    msg = re.sub(r'\s+(A\d?|[\^*?]+)$', '', msg)
    return msg.strip()


def parse_standard_format(filepath):
    """Parses one WSJT-X log file. Returns (decodes, total_lines, tx_lines,
    unparsed_rx_lines) for the parse-yield diagnostic."""
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
                'snr': int(snr_str),
                'msg': msg
            })

    return decodes, total_lines, tx_lines, unparsed_rx_lines


def session_span_minutes(decodes):
    """
    Approximates session length as (last decode's cycle - first decode's
    cycle + one cycle). This is a proxy from decode timestamps, not a
    logged start/stop time -- if a session had a long dead-air stretch at
    the very start or end with zero decodes, this will slightly
    UNDERESTIMATE true elapsed recording time. Good enough for a rate
    comparison; not a substitute for knowing your actual recording length.
    """
    if not decodes:
        return 0.0
    buckets = [d['time_bucket'] for d in decodes]
    span_seconds = (max(buckets) - min(buckets)) + 15
    return span_seconds / 60.0


def main():
    setting_names = list(CLIENT_FILES.keys())

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
        if unparsed_rx_lines > 0:
            print(f"          ^ worth checking manually -- these looked like Rx "
                  f"entries but didn't match the expected format.")

    stats = {}
    for s in setting_names:
        decodes = client_decodes[s]
        snr_list = [d['snr'] for d in decodes]
        weak_count = sum(1 for snr in snr_list if snr <= WEAK_SNR_THRESHOLD)
        span_min = session_span_minutes(decodes)
        stats[s] = {
            'total': len(decodes),
            'snr_list': snr_list,
            'weak_count': weak_count,
            'span_min': span_min
        }

    print("\n" + "="*55)
    print(" WSJT-X DECODE-START SETTING COMPARISON (serial sessions)")
    print(" NOTE: sessions are separate time windows, not simultaneous.")
    print(" Rate and distribution stats below are duration-normalized;")
    print(" differences may still reflect band-condition drift between")
    print(" sessions, not only the decoder setting if not testing the same audio file.")
    print("="*55)

    print("\n--- SESSION SPAN (approx., from first-to-last decode) ---")
    for s in setting_names:
        print(f"{s:<8}: {stats[s]['span_min']:.1f} min, {stats[s]['total']:,} decodes")

    print("\n--- DECODE RATE (decodes per minute) ---")
    for s in setting_names:
        span = stats[s]['span_min']
        rate = stats[s]['total'] / span if span > 0 else 0
        print(f"{s:<8}: {rate:.2f} decodes/min")

    print(f"\n--- WEAK SIGNAL SHARE (<= {WEAK_SNR_THRESHOLD} dB, % of THAT setting's own total) ---")
    for s in setting_names:
        total = stats[s]['total']
        weak = stats[s]['weak_count']
        pct = (weak / total * 100) if total else 0
        print(f"{s:<8}: {pct:.1f}% ({weak:,} of {total:,})")

    print("\n--- DECODED SNR (mean / median / stdev) ---")
    for s in setting_names:
        vals = stats[s]['snr_list']
        if vals:
            mean_snr = statistics.mean(vals)
            median_snr = statistics.median(vals)
            stdev_snr = statistics.stdev(vals) if len(vals) > 1 else 0.0
            print(f"{s:<8}: mean {mean_snr:.2f} dB / median {median_snr:.2f} dB / stdev {stdev_snr:.2f} dB")
        else:
            print(f"{s:<8}: N/A")

    print("\n" + "="*55)


if __name__ == "__main__":
    main()
