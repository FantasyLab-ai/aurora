import csv, sys
from pathlib import Path

import dpkt
import socket

PCAP_IN = Path(r"C:\Users\bgrut\Desktop\Aurora_QIE\fantasyai\data\external\quant_trading\raw\quant_data.pcap")
CSV_OUT = Path(r"C:\Users\bgrut\Desktop\Aurora_QIE\fantasyai\data\external\quant_trading\raw\quant_packets.csv")

def inet_to_str(inet):
    try:
        return socket.inet_ntop(socket.AF_INET, inet)
    except OSError:
        return ""

def main():
    if not PCAP_IN.exists():
        raise FileNotFoundError(f"PCAP not found: {PCAP_IN}")

    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)

    with open(PCAP_IN, "rb") as f, open(CSV_OUT, "w", newline="", encoding="utf-8") as out:
        pcap = dpkt.pcap.Reader(f)
        w = csv.writer(out)
        w.writerow(["time_epoch", "frame_len", "ip_src", "ip_dst", "ip_proto", "tcp_sport", "tcp_dport", "udp_sport", "udp_dport"])

        n = 0
        for ts, buf in pcap:
            n += 1
            frame_len = len(buf)

            ip_src = ip_dst = ""
            ip_proto = ""
            tcp_sport = tcp_dport = ""
            udp_sport = udp_dport = ""

            try:
                eth = dpkt.ethernet.Ethernet(buf)
                if isinstance(eth.data, dpkt.ip.IP):
                    ip = eth.data
                    ip_src = inet_to_str(ip.src)
                    ip_dst = inet_to_str(ip.dst)
                    ip_proto = str(ip.p)

                    if isinstance(ip.data, dpkt.tcp.TCP):
                        tcp_sport = str(ip.data.sport)
                        tcp_dport = str(ip.data.dport)
                    elif isinstance(ip.data, dpkt.udp.UDP):
                        udp_sport = str(ip.data.sport)
                        udp_dport = str(ip.data.dport)
            except Exception:
                # swallow packet parse errors; still keep timestamp + length
                pass

            w.writerow([f"{ts:.6f}", frame_len, ip_src, ip_dst, ip_proto, tcp_sport, tcp_dport, udp_sport, udp_dport])

            # progress print every 1M packets (keeps it from looking frozen)
            if n % 1_000_000 == 0:
                print(f"parsed {n:,} packets...")

    print("Wrote:", CSV_OUT)

if __name__ == "__main__":
    main()
