CREATE TABLE RehabExercises (
    Id INT NOT NULL IDENTITY(1,1) PRIMARY KEY,
    KhopTap NVARCHAR(50) NOT NULL,
    Ten NVARCHAR(100) NOT NULL,
    Upangle NVARCHAR(20) NOT NULL,
    Downangle NVARCHAR(20) NOT NULL,
    DiemA NVARCHAR(50) NOT NULL,
    DiemB NVARCHAR(50) NOT NULL,
    DiemC NVARCHAR(50) NOT NULL,
    HuongDan NVARCHAR(300) NOT NULL
);
GO

INSERT INTO RehabExercises
    (KhopTap, Ten, Upangle, Downangle, DiemA, DiemB, DiemC, HuongDan)
VALUES
-- CHÂN (đầu gối)
(N'Đầu gối', N'Trượt gối',
 N'60-90', N'160-170',
 N'Hông', N'Đầu gối', N'Cổ chân',
 N'Sử dụng đầu gối để co chân. Nâng đầu gối lên xuống'),

(N'Đầu gối', N'Nâng chân thẳng',
 N'30-45', N'0-5',
 N'Hông', N'Đầu gối', N'Cổ chân',
 N'Giữ nguyên chân thẳng, nâng toàn bộ chân lên xuống'),

(N'Đầu gối', N'Ngồi dựa tường',
 N'160-170', N'85-95',
 N'Hông', N'Đầu gối', N'Cổ chân',
 N'Đứng dựa lưng vào tường. Trượt xuống đến góc đầu gối = 90°'),

(N'Đầu gối', N'Gập gối đứng',
 N'100-130', N'40-60',
 N'Hông', N'Đầu gối', N'Cổ chân',
 N'Đứng thẳng, giữ tựa tay vào tường. Gập một đầu gối, đưa gót về phía mông'),

-- TAY (khuỷu tay)
(N'Khuỷu tay', N'Gập/duỗi khuỷu tay',
 N'30-45', N'160-175',
 N'Vai', N'Khuỷu tay', N'Cổ tay',
 N'Gập khuỷu tay. Tay để trên mặt phẳng'),

(N'Khuỷu tay', N'Duỗi tay trên đầu',
 N'160-170', N'40-60',
 N'Vai', N'Khuỷu tay', N'Cổ tay',
 N'Cánh tay dựng thẳng đứng. Gập khuỷu ra sau đầu'),

(N'Khuỷu tay', N'Gập cánh tay đứng',
 N'30-50', N'160-175',
 N'Vai', N'Khuỷu tay', N'Cổ tay',
 N'Gập khuỷu tay. Duỗi hoàn toàn cánh tay'),

(N'Khuỷu tay', N'Duỗi khuỷu nhờ trọng lực',
 N'35-55', N'160-175',
 N'Vai', N'Khuỷu tay', N'Cổ tay',
 N'Giữ nguyên cánh tay lơ lửng trên không');
GO

SELECT
    KhopTap,
    Ten,
    Upangle,
    Downangle,
    DiemA,
    DiemB,
    DiemC,
    HuongDan
FROM dbo.RehabExercises;
GO


CREATE TABLE Patient (
    patient_id INT IDENTITY(1,1) PRIMARY KEY,
    patient_name NVARCHAR(150) NOT NULL,
    date_of_birth DATE NULL,
    gender NVARCHAR(20) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSDATETIME()
);
GO


CREATE TABLE Exercise_adjustment (
    adjustment_id INT IDENTITY(1,1) PRIMARY KEY,
    patient_id INT NOT NULL,
    exercise_id INT NOT NULL,
    session_date DATE NOT NULL,

    pain_count INT NOT NULL DEFAULT 0,
    current_set_count INT NOT NULL,
    current_rep_per_set INT NOT NULL,

    adjustment_action NVARCHAR(20) NULL, -- điều chỉnh như thế nào (tăng/giảm/giữ nguyên)
    adjustment_target NVARCHAR(20) NULL, -- điều chỉnh cái gì (set/rep)
    adjustment_value INT NULL,

    next_set_count INT NULL,
    next_rep_per_set INT NULL,

    adjustment_note NVARCHAR(255) NULL,

    CONSTRAINT FK_patient
        FOREIGN KEY (patient_id) REFERENCES Patient(patient_id),

    CONSTRAINT FK_exercise
        FOREIGN KEY (exercise_id) REFERENCES RehabExercises(Id),

    CONSTRAINT CK_pain_count
        CHECK (pain_count >= 0),

    CONSTRAINT CK_current_set_count
        CHECK (current_set_count > 0),

    CONSTRAINT CK_current_rep_per_set
        CHECK (current_rep_per_set > 0),

    CONSTRAINT CK_next_set_count
        CHECK (next_set_count IS NULL OR next_set_count > 0),

    CONSTRAINT CK_next_rep_per_set
        CHECK (next_rep_per_set IS NULL OR next_rep_per_set > 0),

    CONSTRAINT CK_adjustment_action
        CHECK (
            adjustment_action IS NULL
            OR adjustment_action IN (N'tang', N'giam', N'giu_nguyen')
        ),

    CONSTRAINT CK_adjustment_target
        CHECK (
            adjustment_target IS NULL
            OR adjustment_target IN (N'set', N'rep')
        )
);
GO
