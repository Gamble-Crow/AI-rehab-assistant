import cv2
import math
import time
from ultralytics import YOLO

DANH_SACH_BAI_TAP = {
    "gap_khuyu_tay": {
        "ten": "Gap khuyu tay (Trai)",
        "khop_can_do": (5, 7, 9), # vai - khuyu -tay
        "nguong_duoi": 150,       # Goc khi tay duoi thang ra
        "nguong_gap": 60          # Goc khi tay gap vao dat chuan
    },
    "squat_phuc_hoi": {
        "ten": "Squat tri lieu (Chan Trai)",
        "khop_can_do": (11, 13, 15), # hong - dau goi-mat ca
        "nguong_duoi": 160,          # dung thang
        "nguong_gap": 100            # ngoi xuong 
    }
}

def calculate_angle(a, b, c):
    """Tinh goc 2D giua 3 diem. b la dinh cua goc."""
    radians = math.atan2(c[1]-b[1], c[0]-b[0]) - math.atan2(a[1]-b[1], a[0]-b[0])
    angle = abs(radians * 180.0 / math.pi)
    if angle > 180.0: angle = 360 - angle
    return angle

# init
model = YOLO('yolov8n-pose.pt')
video_source = "http://192.168.5.73:4747/video" 
cap = cv2.VideoCapture(video_source)

# chon bai tap
bai_tap_hien_tai = "squat_phuc_hoi"
thong_tin_bai = DANH_SACH_BAI_TAP[bai_tap_hien_tai]
khop_1, khop_2, khop_3 = thong_tin_bai["khop_can_do"]

# Nao Dem Rep
counter = 0
stage = "DOWN" # down=ngoi, up=dung
prev_time = time.time()

print(f"He thong san sang! Dang chay bai tap: {thong_tin_bai['ten']}")

while cap.isOpened():
    ret, frame = cap.read()
    if not ret: break

    # chay model du doan
    results = model(frame, stream=True, imgsz=320, conf=0.85, verbose=False)
    
    for r in results:
        annotated_frame = r.plot() # ve khung xuong
        
        # kiem tra YOLO co nhin thay nguoi va cac khop khong
        if r.keypoints is not None and len(r.keypoints.xy) > 0:
            # Lay toa do pixel thuc te
            kps = r.keypoints.xy[0].cpu().numpy() 
            
            # Dam bao mang du dai chua cac khop can thiet
            if len(kps) > max(khop_1, khop_2, khop_3):
                p1 = kps[khop_1]
                p2 = kps[khop_2]
                p3 = kps[khop_3]
                
                # che khuat khop do -> loai
                if (p1[0] != 0 and p1[1] != 0) and \
                   (p2[0] != 0 and p2[1] != 0) and \
                   (p3[0] != 0 and p3[1] != 0):
                    
                    # 1. goc
                    angle = calculate_angle(p1, p2, p3)
                    
                    # 2. trang thai
                    # Neu goc vuot nguong duoi thang
                    if angle > thong_tin_bai["nguong_duoi"]:
                        if stage == "DOWN":  # dem chi khi ngoi
                            counter += 1
                            print(f"-> Hoan thanh rep thu: {counter}")
                        stage = "UP"
                    
                    # Neu goc nho hon nguong gap
                    if angle < thong_tin_bai["nguong_gap"]:
                        stage = "DOWN"

                    # 3.hien thi thong 
                    cv2.rectangle(annotated_frame, (0,0), (450, 150), (30, 30, 30), -1)
                    cv2.putText(annotated_frame, f"BAI TAP: {thong_tin_bai['ten']}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                    cv2.putText(annotated_frame, f"GOC: {int(angle)}", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
                    cv2.putText(annotated_frame, f"TRANG THAI: {stage}", (10, 110), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 100, 100), 2)
                    cv2.putText(annotated_frame, f"SO LAN: {counter}", (220, 110), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)

        # Hien thi
        cv2.imshow('AI Phuc Hoi Chuc Nang - YOLO Core', annotated_frame)

    # 'q' de thoat
    if cv2.waitKey(1) & 0xFF == ord('q'): 
        break
        
cap.release()
cv2.destroyAllWindows()