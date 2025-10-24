import sys
import socket
import struct
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

def unpack_packet(pkt: bytes):
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

def send_ack(sock: socket.socket, server_addr, next_expected_seq: int):
    """
    ส่ง ACK (ack_flag=1, data_len=0):
    ค่า ack หมายถึง Next Expected Sequence ลำดับถัดไปที่ Client ต้องการ
    """
    pkt = pack_packet(next_expected_seq-1 if next_expected_seq>0 else 0, next_expected_seq, 1, b"")
    sock.sendto(pkt, server_addr)

def client(server_ip: str, port: int, filename: str):
    """
    ขั้นตอนในฝั่ง Client สำหรับรับไฟล์:
      1) ส่ง Request ไปยัง Server
      2) รับ Packet ตรวจลำดับ (seq) และ Checksum
      3) ถ้า seq == next_expected และ Payload ถูกต้อง จะเขียนลงไฟล์ และส่ง ACK
      4) ถ้า Packet Out of Order หรือ Corrupt จะ Drop และส่ง Duplicate ACK
      5) เมื่อเจอ EOF (ack_flag=1 และ Payload ว่าง) จะตั้งสถานะ eof_received แล้วจบ Loop
      6) มีการตั้ง Timeout บน Socket ถ้าไม่มีอะไรเกิดขึ้น (None) จะส่ง Duplicate ACK เพื่อเร่งให้ Server ส่งมา
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_addr = (server_ip, port)

    # ถ้า Timeout จะส่ง Duplicate ACK เพื่อเร่งการส่งซ้ำจาก Server
    sock.settimeout(1.0)

    
    req = pack_packet(0, 0, 0, filename.encode("utf-8"))
    sock.sendto(req, server_addr)

    print(f"[client] requested {filename} from {server_ip}:{port}")

    # next_expected: เลขลำดับที่ Client คาดว่าจะได้รับถัดไป
    next_expected = 0

    # eof_received: Flag ว่าเจอ EOF แล้วหรือยัง (หยุดเมื่อ True)
    eof_received = False

    # เปิดไฟล์ปลายทาง (Overwrite)
    with open(filename, "wb") as out:
        while True:
            # หยุดเมื่อเจอ EOF จาก Server
            if eof_received:
                break
            try:
                # รอรับ Packet จาก Server ภายในระยะเวลา Timeout
                pkt, addr = sock.recvfrom(2048)
            except socket.timeout:
                # ส่ง Duplicate ACK เพื่อบอก Server ให้ส่งซ้ำ
                send_ack(sock, server_addr, next_expected)
                continue

            # ถ้า Format ไม่ถูกต้อง จะส่ง Duplicate ACK
            try:
                seq, ack, ack_flag, data_len, chk, payload = unpack_packet(pkt)
            except Exception as e:
                send_ack(sock, server_addr, next_expected)
                continue

            # ตรวจ data_len ต้องตรงกับความยาวของ Payload
            if data_len != len(payload):
                send_ack(sock, server_addr, next_expected)
                continue

            # ถ้ามี Payload มีความยาวมากกว่า 0 จะตรวจ Checksum ถ้าไม่ตรงจะ Drop และส่ง Duplicate ACK
            if data_len > 0:
                if internet_checksum(payload) != chk:
                    print(f"[client] drop corrupted seq={seq}")
                    send_ack(sock, server_addr, next_expected)
                    continue

            # รับเฉพาะ In Order (seq ต้องเท่ากับ next_expected)
            if seq == next_expected:
                # ถ้ามีข้อมูลจะเขียน Payload ลงไฟล์
                if data_len > 0:
                    out.write(payload)

                # อัปเดต next_expected (Cumulative ACK)
                next_expected += 1

                # ส่ง ACK ยืนยัน
                send_ack(sock, server_addr, next_expected)

                # ตรวจ EOF ถ้า ack_flag==1 และ Payload ว่างแสดงว่าว่าจบไฟล์
                if ack_flag == 1:
                    eof_received = True
                    print("[client] EOF received")
            else:
                # Out of Order และส่ง Duplicate ACK
                send_ack(sock, server_addr, next_expected)

    print(f"[client] file saved to {filename}")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: client.py <server_ip> <port> <filename>")
        sys.exit(1)
    ip = sys.argv[1]
    port = int(sys.argv[2])
    fname = sys.argv[3]
    client(ip, port, fname)