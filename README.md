# Reliable File Transfer over UDP

## วิธีการ Run

### 1. เริ่มจากการรัน Server

วางไฟล์ที่ต้องการส่งไว้ในโฟลเดอร์เดียวกับ `server.py` แล้วเปิด Terminal / Command Prompt:

```bash
python server.py <port>
```

**ตัวอย่าง:**

```bash
python server.py 9000
```

**Output ตัวอย่าง:**

```
[server] listening on UDP port 9000
[server] simulation settings:
          LOSS_PCT    = 0%
          CORRUPT_PCT = 0%
          WINDOW_SIZE = 10
          RTO_SEC     = 0.5s
```

### 2. รัน Client

เปิดอีก Terminal / Command Prompt แล้วรันคำสั่ง:

```bash
python client.py <server_ip> <port> <filename>
```

**ตัวอย่าง:**

```bash
python client.py 127.0.0.1 9000 test.txt
```

**Output ตัวอย่าง:**

Client:

```
[client] requested test.txt from 127.0.0.1:9000
[client] EOF received
[client] file saved to test.txt
```

จะแสดงข้อความว่าบันทึกไฟล์สำเร็จ

Server:

```
[server] listening on UDP port 9000
[server] simulation settings:
          LOSS_PCT    = 0%
          CORRUPT_PCT = 0%
          WINDOW_SIZE = 10
          RTO_SEC     = 0.5s
[server] client ('127.0.0.1', 55364) requested: test.txt
[server] prepared 1 segment(s) (+EOF)
[server] send seq=0 len=17
[server] send seq=1 len=0 [EOF]
[server] ACK 1
[server] ACK 2
[server] transfer complete in 0.001s for test.txt to ('127.0.0.1', 55364)
```

จะแสดงจำนวนครั้งที่ส่งและความยาว, การ ACK และข้อความว่าส่งไฟล์สำเร็จ

## การจำลองข้อผิดพลาด

ระบบสามารถจำลอง **Packet loss** และ **Data Corruption** ได้ผ่านการกำหนดตัวแปร

| Variable      | Default Value | Description                                           |
| ------------- | ------------- | ----------------------------------------------------- |
| `LOSS_PCT`    | `0`           | เปอร์เซ็นต์ความน่าจะเป็นที่ Packet จะถูก Drop                 |
| `CORRUPT_PCT` | `0`           | เปอร์เซ็นต์ความน่าจะเป็นที่ Payload จะถูกทำให้เสียหาย           |
| `WINDOW_SIZE` | `10`          | ขนาดของ Sliding Window                                |
| `RTO_SEC`     | `0.5`         | เวลาที่รอ ACK (Timeout) ก่อนส่งซ้ำ                         |

### ตัวอย่างการกำหนดตัวแปร

**Windows CMD:**

```bash
set LOSS_PCT=20
set CORRUPT_PCT=10
set WINDOW_SIZE=5
python server.py 9000
```

**Linux / macOS:**

```bash
export LOSS_PCT=20
export CORRUPT_PCT=10
export WINDOW_SIZE=5
python3 server.py 9000
```

## การทดสอบการทำงาน

### **กรณีปกติ (ไม่มีการสูญหายหรือเสียหายของ Packet)**

1. รัน Server โดยไม่ได้ตั้งค่าอะไรเพิ่มเติม (LOSS_PCT=0, CORRUPT_PCT=0)
2. รัน Client เพื่อขอไฟล์จาก Server

```bash
python server.py 9000
python client.py 127.0.0.1 9000 test.txt
```

### **กรณีจำลอง Packet Loss**

ตั้งค่าให้ `LOSS_PCT` = 60:

```bash
set LOSS_PCT=60
python server.py 9000
python client.py 127.0.0.1 9000 test.txt
```

Server จะแสดงการ Retransmit:

```
[server] client ('127.0.0.1', 64142) requested: test.txt
[server] prepared 1 segment(s) (+EOF)
[server] send seq=0 len=17
[server] send seq=1 len=0 [EOF]
[server] ACK 1
[server] timeout -> retransmit 1..1
[server] RESEND seq=1 len=0 [EOF]
[server] ACK 2
[server] transfer complete in 0.505s for test.txt to ('127.0.0.1', 64142)
```

### **กรณีจำลอง Data Corruption**

ตั้งค่าให้ `CORRUPT_PCT` = 30:

```bash
set CORRUPT_PCT=30
python server.py 9000
python client.py 127.0.0.1 9000 test.txt
```

Client จะ Detect และ Drop Packet ที่ Checksum ผิด:

```
[client] requested test.txt from 127.0.0.1:9000
[client] drop corrupted seq=0
[client] EOF received
[client] file saved to test.txt
```



## คำอธิบายเกี่ยวกับระบบ

ระบบนี้จำลองการส่งไฟล์แบบ Reliable Data Transfer ผ่านโปรโตคอล UDP (User Datagram Protocol) โดยใช้ Go-Back-N Sliding Window Protoco* เพื่อป้องกัน Packet Loss และ Data Corruption

Server จะส่งไฟล์ให้ Client เป็น Packet ตามลำดับ (Sequence Number) ส่วน Client จะตอบกลับด้วย ACK เพื่อยืนยันการรับข้อมูลที่ถูกต้องครบถ้วน  

---

### Packet Format

แต่ละแพ็กเกตประกอบด้วย Header และ Payload ตามนี้:

| Field     | Type   | Size (bytes) | Description |
|------------|--------|--------------|--------------|
| seq        | uint32 | 4            | Sequence Number ของ Packet |
| ack        | uint32 | 4            | ACK Number (ใช้เมื่อ ack_flag = 1) |
| ack_flag   | uint8  | 1            | ระบุประเภทของ Packet (0 = Data, 1 = ACK, 2 = EOF) |
| data_len   | uint16 | 2            | ขนาดของ Payload |
| checksum   | uint16 | 2            | Internet Checksum ของ Payload |
| payload    | bytes  | สูงสุด 1024  | ข้อมูลไฟล์ |

**MSS (Maximum Segment Size)** = 1024 bytes  

**EOF Packet** มี `payload = b` และ `ack_flag = 1` เพื่อระบุว่าการส่งไฟล์สิ้นสุดแล้ว

---

### Sequencing

ฝั่ง Server จะกำหนด Sequence Number สำหรับทุก Packet ที่ส่ง ส่วนฝั่ง Client จะตรวจสอบว่า Packet ที่ได้รับตรงกับ next_expected หรือไม่ ถ้าตรงจะทำการเขียนไฟล์และส่ง ACK กลับ ถ้าไม่ตรงจะส่ง Duplicate ACK เพื่อบอก Server ว่า Packet ก่อนหน้ายังไม่ถึง 

---

### Acknowledgment (ACK) Handling

Client จะส่ง ACK โดยใช้ฟังก์ชัน `send_ack()` ซึ่งจะสร้าง Packet ACK ที่มี `ack_flag=1` และ `data_len=0`
ค่า `ack` ที่ส่งกลับมาหมายถึง “next expected sequence”
Server จะรับ ACK และปรับ base ให้เท่ากับค่า ACK ที่ได้รับ เพื่อเลื่อนหน้าต่าง (Sliding Window)

---

### Retransmission and Timeout

Server ใช้ Go-Back-N Sliding Window ด้วย `WINDOW_SIZE` (ค่าเริ่มต้น 10)
กำหนดเวลา Timeout ด้วย `RTO_SEC` (ค่าเริ่มต้น 0.5 วินาที)\
ถ้าเกิด Timeout ขึ้น Server จะ Retransmit ทุก Packet ในช่วง `base` ถึง `nextseq - 1`
เมื่อได้รับ ACK ที่มากกว่า `base` Server จะขยับ Window ต่อไป

---

### Reliability Mechanism (Go-Back-N)

Client จะรับเฉพาะ Packet ที่ถูกต้องตามลำดับ (`seq == next_expected`) ถ้ามี Packet หายหรือเสีย Client จะส่ง Duplicate ACK เพื่อให้ Server Retransmit\
ฝั่ง Server สามารถส่งได้ต่อเนื่องสูงสุดเท่ากับขนาด `WINDOW_SIZE`\
ฝั่ง Client จะรับเฉพาะ Packet ที่มาตรงตามลำดับ (In Order) เท่านั้น ถ้าได้รับ Packet ที่มี `seq` ตรงกับ `next_expected` จะรับไว้แล้วทำการเขียนไฟล์ และส่ง ACK (`ack = next_expected + 1`) ถ้ามี `seq` ไม่ตรงหรือข้ามลำดับ จะ Drop Packet นั้นแล้วส่ง Duplicate ACK สำหรับ Packet ล่าสุดที่ได้รับมา และถูกต้อง

---

### Integrity Check

ทุก Packet จะมีการ Checksum Payload โดยฝั่ง Client ทุกครั้ง ถ้าผิด Packet นั้นจะถูก Drop และส่ง Duplicate ACK

---

### Error Simulation

การจำลอง Network Error ใน `server.py` ผ่านตัวแปรต่างๆ :

| Variable     | Default | Description |
|---------------|----------|-------------|
| LOSS_PCT      | 0        | เปอร์เซ็นต์ความน่าจะเป็นที่ Packet จะถูก Drop |
| CORRUPT_PCT   | 0        | เปอร์เซ็นต์ความน่าจะเป็นที่ Payload จะเสียหาย |
| WINDOW_SIZE   | 10       | ขนาดของ Sliding Window |
| RTO_SEC       | 0.5      | เวลาที่รอ ACK ก่อน Timeout |

โดยใช้ฟังก์ชัน
- `maybe_drop()` จำลอง Packet Loss
- `maybe_corrupt(payload)` จำลองข้อมูลเสีย โดยการสุ่มเปลี่ยน Byte หนึ่งตำแหน่ง

ใช้ฟังก์ชัน `maybe_drop()` เพื่อสุ่ม Drop Packet ตามค่าที่ตั้งใน `LOSS_PCT` เช่น `set LOSS_PCT=20` หมายถึง มีโอกาส 20% ที่ packet จะหาย

---

### Limitations

- ไม่รองรับ Out of Order Delivery Client จะ Drop Packet ที่มาช้าและไม่เรียงลำดับ (เนื่องจากใช้ Go-Back-N)
- ไม่มี Dynamic Timeout ค่าของ RTO_SEC เป็นค่าคงที่ ไม่ได้ปรับตาม RTT จริง
- ไม่มี Congestion Control ไม่มีการปรับ Window ตาม Network Congestion
- ไม่มี Selective Repeat ถ้า Packet เดียวหาย จะต้อง Retransmit ทั้ง Window

---

### Future Improvements

ปรับปรุงให้รองรับ Selective Repeat เพื่อให้ส่งซ้ำเฉพาะ Packet ที่หาย, เพิ่มการคำนวณ RTT Estimation เพื่อปรับค่า Timeout แบบ Adaptive และเพิ่ม Congestion Control Mechanism

---

คลิปวิดีโอแสดงตัวอย่างการทำงาน : https://youtu.be/ylNR_CA1xQY