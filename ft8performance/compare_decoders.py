import re
import os
import csv
from collections import defaultdict
import statistics

CLIENT_FILES = {
    "WSJT-X": "wsjtx_all.txt",
    "MSHV": "mshv_all.txt",
    "JTDX": "jtdx_all.txt",
    "Nexus": "nexus_all.txt"
}

CLIENT_FORMATS = {
    "WSJT-X": "standard",
    "JTDX": "jtdx",
    "Nexus": "standard",
    "MSHV": "mshv"
}

WEAK_SNR_THRESHOLD = -20

STANDARD_PATTERN = re.compile(
    r'^(?P<date>\d{6,8})_(?P<time>\d{6})\s+'
    r'[\d.]+\s+Rx\s+\S+\s+'
    r'(?P<snr>-?\d+)\s+'
    r'[^ \t]+\s+'                
    r'\d+\s+'                    
    r'(?P<msg>.+)$',
    re.IGNORECASE
)

JTDX_PATTERN = re.compile(
    r'^(?P<date>\d{6,8})_(?P<time>\d{6})\s+'
    r'(?P<snr>-?\d+)\s+'
    r'[^ \t]+\s+'                
    r'\d+\s+'                    
    r'~?\s*'                     
    r'(?P<msg>.+)$',
    re.IGNORECASE
)

MSHV_PATTERN = re.compile(
    r'^(?P<day>\d+)\|'
    r'RX\s+[\d.]+\s+\S+\|'
    r'(?P<time>\d{6})\|'
    r'(?P<snr>-?\d+)\|'
    r'[^|]+\|'                   
    r'[^|]+\|'                   
    r'(?P<msg>[^|]+)\|'          
    r'.*$',                      
    re.IGNORECASE
)

def time_to_seconds(time_str):
    h, m, s = int(time_str[0:2]), int(time_str[2:4]), int(time_str[4:6])
    return h * 3600 + m * 60 + s

def clean_message(msg):
    msg = msg.strip().upper()
    msg = re.sub(r'^[~?]\s*', '', msg)
    msg = re.sub(r'\s+([AP]\d?|[\?\^\*]+)$', '', msg)
    return msg.strip()

def parse_file(filepath, fmt):
    if not os.path.exists(filepath):
        print(f"Warning: File {filepath} not found.")
        return [], 0, 0

    raw_decodes = []
    total_lines = 0
    tx_lines = 0

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
                
            total_lines += 1
            
            # Count transmit lines to exclude them from the parse yield expectation
            if " Tx " in line or "|TX " in line or "Transmitting" in line:
                tx_lines += 1
                continue
            
            if fmt == "standard":
                match = STANDARD_PATTERN.match(line)
            elif fmt == "jtdx":
                match = JTDX_PATTERN.match(line)
            elif fmt == "mshv":
                match = MSHV_PATTERN.match(line)
            else:
                continue
                
            if not match:
                continue

            if fmt == "mshv":
                day_str = match.group('day')
            else:
                day_str = match.group('date')[-2:] 
                
            time_str = match.group('time')
            snr_str = match.group('snr')
            raw_msg = match.group('msg')

            msg = clean_message(raw_msg)
            if len(msg) <= 3:
                continue

            try:
                raw_decodes.append({
                    'day': day_str,
                    'total_seconds': time_to_seconds(time_str),
                    'snr': int(snr_str),
                    'msg': msg
                })
            except ValueError:
                continue
                
    if not raw_decodes:
        return [], total_lines, tx_lines
        
    exact_boundary_count = sum(1 for d in raw_decodes if d['total_seconds'] % 15 == 0)
    is_write_time_logger = (exact_boundary_count / len(raw_decodes)) < 0.5 
    
    final_decodes = []
    for d in raw_decodes:
        secs = d['total_seconds']
        if is_write_time_logger:
            secs -= 15 
        d['time_bucket'] = (secs // 15) * 15
        final_decodes.append(d)
            
    return final_decodes, total_lines, tx_lines

def main():
    client_names = list(CLIENT_FILES.keys())
    client_raw_decodes = {}
    stats = {client: {'total': 0, 'unique': 0, 'weak_count': 0, 'snr_list': [], 'missed': 0} for client in client_names}

    print("Parsing files and analyzing timing sync. This might take a moment...")
    
    print("\n--- PARSE YIELD (Sanity Check) ---")
    for client, filename in CLIENT_FILES.items():
        decodes, total_lines, tx_lines = parse_file(filename, CLIENT_FORMATS[client])
        client_raw_decodes[client] = decodes
        
        rx_lines = total_lines - tx_lines
        parsed_count = len(decodes)
        yield_pct = (parsed_count / rx_lines * 100) if rx_lines > 0 else 0
        
        print(f"{client:<10}: {parsed_count:,} parsed out of {rx_lines:,} potential RX lines ({yield_pct:.1f}%)")
        
        for d in decodes:
            stats[client]['total'] += 1
            stats[client]['snr_list'].append(d['snr'])
            if d['snr'] <= WEAK_SNR_THRESHOLD:
                stats[client]['weak_count'] += 1

    master_log = defaultdict(dict)
    for client, decodes in client_raw_decodes.items():
        for d in decodes:
            key = (d['day'], d['time_bucket'], d['msg'])
            # If a client decodes the same message twice in a 15s window (rare but possible), keep the best SNR
            if client in master_log[key]:
                master_log[key][client] = max(master_log[key][client], d['snr'])
            else:
                master_log[key][client] = d['snr']

    total_unique_signals = len(master_log)
    decoded_by_all = 0

    # For pairwise WSJT-X vs JTDX overlap check
    wsjtx_jtdx_overlap = 0

    for key, decoders in master_log.items():
        decoder_list = list(decoders.keys())
        
        if len(decoder_list) == len(client_names):
            decoded_by_all += 1
            
        if "WSJT-X" in decoders and "JTDX" in decoders:
            wsjtx_jtdx_overlap += 1
            
        if len(decoder_list) == 1:
            stats[decoder_list[0]]['unique'] += 1

        for client in client_names:
            if client not in decoders:
                stats[client]['missed'] += 1

    print("\n" + "="*50)
    print(" FT8 DECODER PERFORMANCE REPORT")
    print("="*50)
    
    print(f"\nOverall Unique Signals Heard Across All Clients: {total_unique_signals:,}")
    print(f"Signals Decoded by EVERY Client Simultaneously : {decoded_by_all:,}")
    
    if "WSJT-X" in client_names and "JTDX" in client_names:
        print(f"Signals shared by WSJT-X and JTDX              : {wsjtx_jtdx_overlap:,}")
    
    print("\n--- TOTAL DECODES ---")
    for client in client_names:
        print(f"{client:<10}: {stats[client]['total']:,} decodes")

    print("\n--- UNIQUE DECODES (Signals NO ONE else decoded) ---")
    for client in client_names:
        print(f"{client:<10}: {stats[client]['unique']:,} decodes")

    print(f"\n--- WEAK SIGNAL PERFORMANCE (<= {WEAK_SNR_THRESHOLD} dB) ---")
    for client in client_names:
        print(f"{client:<10}: {stats[client]['weak_count']:,} weak signals decoded")

    print("\n--- AVERAGE DECODED SNR ---")
    for client in client_names:
        if stats[client]['snr_list']:
            avg_snr = statistics.mean(stats[client]['snr_list'])
            print(f"{client:<10}: {avg_snr:.2f} dB")

    print("\n--- MISSED DECODES (Heard by at least one other client) ---")
    for client in client_names:
        print(f"{client:<10}: {stats[client]['missed']:,} signals missed")
        
    print("\n" + "="*50)

    # --- CSV Export for Manual Audit ---
    csv_filename = "decode_audit_log.csv"
    try:
        with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            headers = ["Day", "Time_Bucket", "Message"] + [f"{c}_SNR" for c in client_names]
            writer.writerow(headers)
            
            # Sort chronologically
            sorted_keys = sorted(master_log.keys(), key=lambda x: (x[0], x[1]))
            for key in sorted_keys:
                day, time_bucket, msg = key
                row = [day, time_bucket, msg]
                for client in client_names:
                    row.append(master_log[key].get(client, ""))
                writer.writerow(row)
        print(f"Audit log saved to '{csv_filename}'. Open this in Excel to manually verify the matching logic.")
    except Exception as e:
        print(f"Failed to write CSV: {e}")

if __name__ == "__main__":
    main()
