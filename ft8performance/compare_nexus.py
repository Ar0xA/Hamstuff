##compare decodes of 2 nexus clients from teh same .wav. this ignores timestamps and just looks at the decode windows
import re
import os
import csv
from collections import defaultdict
import statistics

# Set just the two Nexus files for comparison
CLIENT_FILES = {
    "Nexus_Old": "nexus_all_old.txt",
    "Nexus_New": "nexus_all_new.txt"
}

WEAK_SNR_THRESHOLD = -20

# Standard WSJT-X format pattern
STANDARD_PATTERN = re.compile(
    r'^(?P<date>\d{6,8})_(?P<time>\d{6})\s+'
    r'[\d.]+\s+Rx\s+\S+\s+'
    r'(?P<snr>-?\d+)\s+'
    r'[^ \t]+\s+'                 
    r'\d+\s+'                     
    r'(?P<msg>.+)$',
    re.IGNORECASE
)

def clean_message(msg):
    msg = msg.strip().upper()
    msg = re.sub(r'^[~?]\s*', '', msg)
    msg = re.sub(r'\s+([AP]\d?|[\?\^\*]+)$', '', msg)
    return msg.strip()

def parse_file(filepath):
    if not os.path.exists(filepath):
        print(f"Warning: File {filepath} not found.")
        return [], 0, 0

    decodes = []
    total_lines = 0
    tx_lines = 0

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
                
            total_lines += 1
            
            # Count transmit lines to exclude them
            if " Tx " in line or "|TX " in line or "Transmitting" in line:
                tx_lines += 1
                continue
            
            match = STANDARD_PATTERN.match(line)
            if not match:
                continue

            snr_str = match.group('snr')
            raw_msg = match.group('msg')

            msg = clean_message(raw_msg)
            if len(msg) <= 3:
                continue

            try:
                decodes.append({
                    'snr': int(snr_str),
                    'msg': msg
                })
            except ValueError:
                continue
                
    return decodes, total_lines, tx_lines

def main():
    client_names = list(CLIENT_FILES.keys())
    client_raw_decodes = {}
    stats = {client: {'total': 0, 'unique': 0, 'weak_count': 0, 'snr_list': [], 'missed': 0} for client in client_names}

    print("Parsing files and mapping unique messages. This might take a moment...")
    
    print("\n--- PARSE YIELD (Sanity Check) ---")
    for client, filename in CLIENT_FILES.items():
        decodes, total_lines, tx_lines = parse_file(filename)
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

    # Match purely by the message string, ignoring time entirely
    master_log = defaultdict(dict)
    
    for client, decodes in client_raw_decodes.items():
        for d in decodes:
            msg = d['msg']
            snr = d['snr']
            
            # If the same message is decoded multiple times in a file, keep the best SNR
            if client in master_log[msg]:
                master_log[msg][client] = max(master_log[msg][client], snr)
            else:
                master_log[msg][client] = snr

    total_unique_signals = len(master_log)
    decoded_by_all = 0

    for msg, decoders in master_log.items():
        decoder_list = list(decoders.keys())
        
        if len(decoder_list) == len(client_names):
            decoded_by_all += 1
            
        if len(decoder_list) == 1:
            stats[decoder_list[0]]['unique'] += 1

        for client in client_names:
            if client not in decoders:
                stats[client]['missed'] += 1

    print("\n" + "="*50)
    print(" NEXUS VERSION PERFORMANCE REPORT")
    print("="*50)
    
    print(f"\nOverall Unique Messages Heard Across Both Versions : {total_unique_signals:,}")
    print(f"Messages Decoded by BOTH Versions                  : {decoded_by_all:,}")
    
    print("\n--- TOTAL DECODES ---")
    for client in client_names:
        print(f"{client:<10}: {stats[client]['total']:,} decodes")

    print("\n--- UNIQUE DECODES (Messages the other version missed) ---")
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

    print("\n--- MISSED DECODES (Heard by the other version but not this one) ---")
    for client in client_names:
        print(f"{client:<10}: {stats[client]['missed']:,} messages missed")
        
    print("\n" + "="*50)

    # --- CSV Export for Manual Audit ---
    csv_filename = "nexus_compare_audit_log.csv"
    try:
        with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            headers = ["Message"] + [f"{c}_Best_SNR" for c in client_names]
            writer.writerow(headers)
            
            # Sort alphabetically by message
            sorted_msgs = sorted(master_log.keys())
            for msg in sorted_msgs:
                row = [msg]
                for client in client_names:
                    row.append(master_log[msg].get(client, ""))
                writer.writerow(row)
        print(f"Audit log saved to '{csv_filename}'.")
    except Exception as e:
        print(f"Failed to write CSV: {e}")

if __name__ == "__main__":
    main()
