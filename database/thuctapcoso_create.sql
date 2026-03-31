CREATE TABLE RehabExercises (
    Id INT NOT NULL IDENTITY(1,1) PRIMARY KEY,              
    KhopTap NVARCHAR(50) NOT NULL,               
    Ten NVARCHAR(100) NOT NULL,               
    Upangle NVARCHAR(20) NOT NULL,               
    Downangle NVARCHAR(20) NOT NULL,               
    DiemA NVARCHAR(50) NOT NULL,               
    DiemB NVARCHAR(50) NOT NULL,               
    DiemC NVARCHAR(50) NOT NULL,              
    HuongDan NVARCHAR(300) NOT NULL,                      
);
GO

INSERT INTO RehabExercises
    (KhopTap, Ten, Upangle, Downangle, DiemA, DiemB, DiemC, HuongDan)
VALUES
 
-- CHÂN (đầu gối)
(N'Đầu gối', N'Trượt gối',
 '60-90', '160-170',
 N'Hông', N'Đầu gối', N'Cổ chân',
 N'Sử dụng đầu gối để co chân. Nâng đầu gối lên xuống'),
 
(N'Đầu gối', N'Nâng chân thẳng',
 '30-45', '0-5',
 N'Hông', N'Đầu gối', N'Cổ chân',
 N'Giữ nguyên chân thẳng, nâng toàn bộ chân lên xuống'),
 
(N'Đầu gối', N'Ngồi dựa tường',
 '160-170', '85-95',
 N'Hông', N'Đầu gối', N'Cổ chân',
 N'Đứng dựa lưng vào tường. Trượt xuống đến góc đầu gối = 90°'),
 
(N'Đầu gối', N'Gập gối đứng',
 '100-130', '150-175',
 N'Hông', N'Đầu gối', N'Cổ chân',
 N'Đứng thẳng, giữ tựa tay vào tường. Gập một đầu gối, đưa gót về phía mông'),
 
-- TAY (khuỷu tay)
(N'Khuỷu tay', N'Gập/duỗi khuỷu tay',
 '30-45', '160-175',
 N'Vai', N'Khuỷu tay', N'Cổ tay',
 N'Gập khuỷu tay. Tay để trên mặt phẳng'),
 
(N'Khuỷu tay', N'Duỗi tay trên đầu',
 '160-170', '40-60',
 N'Vai', N'Khuỷu tay', N'Cổ tay',
 N'Cánh tay dựng thẳng đứng. Gập khuỷu ra sau đầu'),
 
(N'Khuỷu tay', N'Gập cánh tay đứng',
 '30-50', '160-175',
 N'Vai', N'Khuỷu tay', N'Cổ tay',
 N'Gập khuỷu tay. Duỗi hoàn toàn cánh tay'),
 
(N'Khuỷu tay', N'Duỗi khuỷu nhờ trọng lực',
 '35-55', '160-175',
 N'Vai', N'Khuỷu tay', N'Cổ tay',
 N'Giữ nguyên cánh tay lơ lửng trên không');
GO

SELECT
    KhopTap as[Bộ phận tập],
    Ten as[Tên bài],
    Upangle as[Góc gập lên],      
    Downangle as[Góc gập xuống],     
    DiemA as[Điểm trên],           
    DiemB as[Điểm giữa],           
    DiemC as[Điểm cuối],           
    HuongDan as[Hướng dẫn]           
FROM RehabExercises
GO
