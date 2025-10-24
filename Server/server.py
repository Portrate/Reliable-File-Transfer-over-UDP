import os
import sys
import socket
import struct
import random
import time
from typing import Tuple

# ขนาดข้อมูลสูงสุดในแต่ละ Packet (Payload) หน่วยเป็น Byte
MSS = 1024

# โครงสร้างของ Header
# I : uint32  = seq (หมายเลขลำดับของ Packet)
# I : uint32  = ack (เลขของ ACK จากฝั่งรับ ใช้เมื่อเป็น Packet ACK)
# B : uint8   = ack_flag (0=Data, 1=ACK/EOF)
# H : uint16  = data_len (จำนวน Byte ของ Payload)
# H : uint16  = checksum (Internet Checksum ของ Payload)
HDR_FMT = "!IIBHH"

# ความยาวของ Header ที่คำนวณจาก HDR_FMT
HDR_LEN = struct.calcsize(HDR_FMT)

# LOSS_PCT: เปอร์เซ็นต์ความน่าจะเป็นที่ Packet จะถูกดรอป (จำลองการสูญหาย)
LOSS_PCT = int(os.getenv("LOSS_PCT", "0"))

# CORRUPT_PCT: เปอร์เซ็นต์ความน่าจะเป็นที่ Payload จะถูกแก้ให้เสียหาย (จำลอง Corruption)
CORRUPT_PCT = int(os.getenv("CORRUPT_PCT", "0"))

# WINDOW_SIZE: Go-Back-N Window Size
WINDOW_SIZE = int(os.getenv("WINDOW_SIZE", "10"))

# RTO_SEC: เวลา Timeout (วินาที) ที่ Server จะรอรับ ACK ก่อนที่จะส่งซ้ำทั้ง Window
RTO_SEC = float(os.getenv("RTO_SEC", "0.5"))



def internet_checksum(data: bytes) -> int:
    """
    คำนวณ Internet Checksum สำหรับ Payload
    ถ้าจำนวนของ Byte เป็นเลขคี่จะเติม 0x00 หนึ่ง Byte ก่อนการคำนวณ
    """
    if len(data) % 2 == 1:
        data += b'\x00'
    s = 0
    for i in range(0, len(data), 2):
        w = (data[i] << 8) + data[i+1]
        s += w
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF

def pack_packet(seq: int, ack: int, ack_flag: int, payload: bytes) -> bytes:
    """
    สร้าง Packet สำหรับส่งออก
    โดยคำนวณ Checksum จาก Payload (ถ้า Payload ว่าง ให้ Checksum=0)
    สร้าง Header และ Payload ให้เป็น Byte เพื่อให้พร้อมสำหรับการส่ง
    """
    data_len = len(payload)
    chk = internet_checksum(payload) if data_len > 0 else 0
    header = struct.pack(HDR_FMT, seq, ack, ack_flag & 0xFF, data_len, chk)
    return header + payload

def unpack_packet(pkt: bytes) -> Tuple[int,int,int,int,int,bytes]:
    """
    แยก Packet ออกเป็น Field ต่างๆ และ Payload
    Return (seq, ack, ack_flag, dataLen, checksum, payload)
    ถ้า Packet สั้นกว่า HDR_LEN ให้แสดง Error
    """
    if len(pkt) < HDR_LEN:
        raise ValueError("packet too short")
    seq, ack, ack_flag, data_len, chk = struct.unpack(HDR_FMT, pkt[:HDR_LEN])
    payload = pkt[HDR_LEN:HDR_LEN+data_len]
    return seq, ack, ack_flag, data_len, chk, payload

def maybe_drop() -> bool:
    """
    จะคืนค่า True ถ้ามีการจำลองเหตุการณ์ให้ Drop Packet
    """
    return random.randrange(100) < LOSS_PCT

def maybe_corrupt(payload: bytes) -> bytes:
    """
    จำลองการทำให้ข้อมูลเสียหาย โดยสุ่มเลือก Byte หนึ่งตำแหน่งใน Payload แล้วสลับบิตด้วย XOR 0xFF
    ถ้า Payload ว่างจะไม่ได้ทำอะไร
    """
    if len(payload) == 0:
        return payload
    if random.randrange(100) < CORRUPT_PCT:
        b = bytearray(payload)
        pos = random.randrange(len(b))
        b[pos] ^= 0xFF
        return bytes(b)
    return payload



# Go-Back-N

def load_file_chunks(path: str) -> list:
    """
    อ่านไฟล์และแบ่งเป็นส่วนๆ ตาม MSS
    คืนค่าเป็น List ของ Byte
    """
    chunks = []
    with open(path, "rb") as f:
        while True:
            buf = f.read(MSS)
            if not buf:
                break
            chunks.append(buf)
    return chunks

def sendto_maybe(sock: socket.socket, pkt: bytes, addr):
    """
    ส่ง Packet ผ่าน UDP พร้อมจำลองเงื่อนไขของ Network
    ขั้นตอน:
      1) สุ่มตัดสินใจว่าจะ Drop Packet หรือไม่
      2) ถ้าไม่ Drop ให้แตก Packet เพื่อดึง Payload ออกมาจำลอง Corruption (ถ้ามี)
      3) ถ้าเกิด Corruption จะคงค่า Checksum เดิมเอาไว้เพื่อให้ฝั่ง Client ตรวจสอบ
      4) ส่ง Packet ไปยังปลายทาง
    """
    if maybe_drop():
        return
    seq, ack, ack_flag, data_len, chk, payload = unpack_packet(pkt)
    corrupted_payload = maybe_corrupt(payload)
    if corrupted_payload is not payload:
        header = struct.pack(HDR_FMT, seq, ack, ack_flag & 0xFF, data_len, chk)
        pkt = header + corrupted_payload
    sock.sendto(pkt, addr)

def server(port: int):
    """
    การทำงานของ Server สำหรับการส่งไฟล์แบบ Go-Back-N:
      1) รอ Request จาก Client
      2) ตรวจสอบไฟล์
      3) แปลงไฟล์เป็น Segment (Payload) และเตรียมส่งด้วย Go-Back-N
      4) ใช้ตัวแปร base/nextseq เพื่อควบคุมการส่งและการเลื่อนหน้าต่าง
      5) ตั้ง timeout = RTO_SEC และถ้ารับ ACK ได้ไม่ทันเวลา ให้ส่งซ้ำช่วง base ถึง nextseq-1
      6) ส่ง EOF (payload ว่าง + ack_flag=1) เพื่อบอกว่าเป็น Packet สุดท้าย
    """
    random.seed()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    print(f"[server] listening on UDP port {port}")
    print(f"[server] simulation settings:")
    print(f"          LOSS_PCT    = {LOSS_PCT}%")
    print(f"          CORRUPT_PCT = {CORRUPT_PCT}%")
    print(f"          WINDOW_SIZE = {WINDOW_SIZE}")
    print(f"          RTO_SEC     = {RTO_SEC}s")

    # Block ระหว่างการเริ่มใหม่ในแต่ละรอบ
    sock.settimeout(None)

    while True:
        # 1) รอรับ Request จาก Client
        try:
            data, client_addr = sock.recvfrom(2048)
        except (socket.timeout, ConnectionResetError):
            # กรณีเกิด Timeout หรือการเชื่อมต่อ Reset ระหว่างรอ Request จะเริ่มการรอใหม่
            continue

        # Unpack Request Packet หากผิดรูปแบบให้ข้ามไป
        try:
            _s, _a, _af, _dl, _chk, payload = unpack_packet(data)
        except Exception as e:
            print(f"[server] bad request from {client_addr}: {e}")
            continue

        # แปลง Payload เป็นชื่อไฟล์
        filename = payload.decode("utf-8", errors="ignore").strip()
        if not filename:
            print(f"[server] empty filename from {client_addr}")
            continue

        # ตรวจสอบว่าไฟล์มีอยู่จริง
        if not (os.path.exists(filename) and os.path.isfile(filename)):
            print(f"[server] file not found: {filename}  (cwd={os.getcwd()})")
            continue

        print(f"[server] client {client_addr} requested: {filename}")

        # 2) เตรียมข้อมูลเป็น Segment
        segments = load_file_chunks(filename)
        total = len(segments) + 1  # +1 สำหรับ EOF Packet

        if len(segments) == 0:
            print(f"[server] WARNING: file is empty -> will send only EOF")
        else:
            print(f"[server] prepared {len(segments)} segment(s) (+EOF)")

        # base: หมายเลข Packet แรกในหน้าต่างที่ยังไม่ได้รับ ACK
        base = 0

        # nextseq: หมายเลข Packet ถัดไปที่พร้อมจะส่ง
        nextseq = 0

        # ระหว่างส่งข้อมูลตั้ง Timeout
        sock.settimeout(RTO_SEC)
        start_time = time.time()

        try:
            # วนส่งจนกว่าจะได้รับ ACK ครบทุก Packet (รวม EOF)
            while base < total:
                # 2.1) ส่ง Packet ใหม่เพิ่มได้ ถ้าไม่เกินขนาดของ WINDOW_SIZE
                while nextseq < total and (nextseq - base) < WINDOW_SIZE:
                    if nextseq < len(segments):
                        payload = segments[nextseq]
                        ack_flag = 0   # 0 = Packet ทั่วไป
                    else:
                        payload = b""
                        ack_flag = 1   # 1 = EOF
                    pkt = pack_packet(nextseq, 0, ack_flag, payload)
                    print(f"[server] send seq={nextseq} len={len(payload)}{' [EOF]' if ack_flag==1 else ''}")
                    sendto_maybe(sock, pkt, client_addr)
                    nextseq += 1

                # 2.2) รอรับ ACK หรือ Timeout
                try:
                    pkt, _ = sock.recvfrom(2048)
                    s, a, af, dl, c, pl = unpack_packet(pkt)

                    # ถ้าเลข ACK (a) มากกว่า base แปลว่ามีการรับข้อมูลเพิ่ม จะเลื่อน Window ไปข้างหน้า
                    if a > base:
                        print(f"[server] ACK {a}")
                        base = a
                except socket.timeout:
                    # 2.3) กรณีหมดเวลา Go-Back-N จะส่งซ้ำทุกแพ็กเก็ตตั้งแต่ base ถึง nextseq-1
                    print(f"[server] timeout -> retransmit {base}..{nextseq-1}")
                    for resend in range(base, nextseq):
                        if resend < len(segments):
                            payload = segments[resend]
                            ack_flag = 0
                        else:
                            payload = b""
                            ack_flag = 1
                        pkt = pack_packet(resend, 0, ack_flag, payload)
                        print(f"[server] RESEND seq={resend} len={len(payload)}{' [EOF]' if ack_flag==1 else ''}")
                        sendto_maybe(sock, pkt, client_addr)
                except ConnectionResetError:
                    # ให้ส่งแล้วกลับไปรอ Request ใหม่
                    print(f"[server] client reset during transfer: {client_addr}")
                    break
        finally:
            # 3) สรุปเวลาที่ใช้ คืนค่า Timeout และรอ Request ถัดไป
            elapsed = time.time() - start_time
            print(f"[server] transfer complete in {elapsed:.3f}s for {filename} to {client_addr}")
            sock.settimeout(None)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: server.py <port>")
        sys.exit(1)
    port = int(sys.argv[1])
    server(port)