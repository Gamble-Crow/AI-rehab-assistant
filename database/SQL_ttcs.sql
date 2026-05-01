create database ttcs;
go

use ttcs;
go

create table rehab_exercises (
    id           int not null identity(1,1) primary key,
    khop_tap     nvarchar(50)  not null,
    ten          nvarchar(100) not null,
    up_angle     nvarchar(20)  not null,
    down_angle   nvarchar(20)  not null,
    diem_a       nvarchar(50)  not null,
    diem_b       nvarchar(50)  not null,
    diem_c       nvarchar(50)  not null,
    huong_dan    nvarchar(300) not null
);
go
 
create table patient (
    patient_id    int identity(1,1) primary key,
    patient_name  nvarchar(150) not null,
    date_of_birth date          null,
    gender        nvarchar(20)  null,
    created_at    datetime2     not null default sysdatetime()
);
go
 
-- cuong do dang ap dung hien tai cua tung benh nhan / bai tap
create table current_config (
    config_id    int identity(1,1) primary key,
    patient_id   int       not null,
    exercise_id  int       not null,
    current_rep  int       not null default 10,
    updated_at   datetime2 not null default sysdatetime(),
    constraint fk_cc_patient  foreign key (patient_id)  references patient(patient_id),
    constraint fk_cc_exercise foreign key (exercise_id) references rehab_exercises(id),
    constraint uq_cc          unique (patient_id, exercise_id),
    constraint ck_cc_rep      check (current_rep > 0)
);
go
 
-- ghi nhan du lieu thuc te trong buoi tap
create table session_log (
    session_log_id      int identity(1,1) primary key,
    patient_id      int  not null,
    exercise_id     int  not null,
    session_date    date not null,
    prescribed_rep  int  not null,   -- rep duoc chi dinh dau buoi (lay tu current_config)
    actual_rep      int  not null,   -- rep thuc te camera dem duoc
    pain_count      int  not null default 0,
    constraint fk_sl_patient  foreign key (patient_id)  references patient(patient_id),
    constraint fk_sl_exercise foreign key (exercise_id) references rehab_exercises(id),
    constraint ck_sl_prescribed check (prescribed_rep > 0),
    constraint ck_sl_actual     check (actual_rep >= 0),
    constraint ck_sl_pain       check (pain_count >= 0)
);
go
 
-- de xuat dieu chinh sau buoi tap (chi luu khi benh nhan dong y)
create table exercise_adjustment (
    adjustment_id     int identity(1,1) primary key,
    session_log_id        int           not null,
    patient_id        int           not null,
    exercise_id       int           not null,
    adjustment_date   date          not null,
    current_rep       int           not null,   -- cuong do tai thoi diem de xuat
    suggested_rep     int           not null,   -- de xuat cua bo nao
    adjustment_action nvarchar(20)  null,       -- tang / giam / giu_nguyen (co the tinh tu 2 bien tren)
    adjustment_note   nvarchar(255) null,
    is_confirmed      bit           not null default 0,
    confirmed_at      datetime2     null,
    constraint fk_ea_session  foreign key (session_log_id)  references session_log(session_log_id),
    constraint fk_ea_patient  foreign key (patient_id)  references patient(patient_id),
    constraint fk_ea_exercise foreign key (exercise_id) references rehab_exercises(id),
    constraint ck_ea_current  check (current_rep > 0),
    constraint ck_ea_suggest  check (suggested_rep > 0),
    constraint ck_ea_action   check (
        adjustment_action is null or adjustment_action in ('tang', 'giam', 'giu_nguyen')
    )
);
go
 
-- stored procedure 
-- lay cuong do hien tai cua benh nhan (feed vao dau buoi moi)
-- neu chua co ban ghi → tra ve null → app dung gia tri mac dinh
create proc sp_get_current_rep
    @patient_id  int,
    @exercise_id int
as
begin
    set nocount on;
    select current_rep
    from current_config
    where patient_id  = @patient_id
      and exercise_id = @exercise_id;
end;
go
 
-- xac nhan de xuat: benh nhan bam dong y
-- cap nhat is_confirmed + cap nhat current_config
create proc sp_confirm_adjustment
    @adjustment_id int
as
begin
    set nocount on;
 
    declare @patient_id   int;
    declare @exercise_id  int;
    declare @suggested_rep int;
 
    -- lay thong tin tu ban ghi de xuat
    select
        @patient_id    = patient_id,
        @exercise_id   = exercise_id,
        @suggested_rep = suggested_rep
    from exercise_adjustment
    where adjustment_id = @adjustment_id;
 
    begin transaction;
    begin try
        -- 1. danh dau da xac nhan
        update exercise_adjustment
        set is_confirmed = 1,
            confirmed_at = sysdatetime()
        where adjustment_id = @adjustment_id;
 
        -- 2. cap nhat cuong do hien tai
        if exists (
            select 1 from current_config
            where patient_id = @patient_id and exercise_id = @exercise_id
        )
            update current_config
            set current_rep = @suggested_rep,
                updated_at  = sysdatetime()
            where patient_id  = @patient_id
              and exercise_id = @exercise_id;
        else
            insert into current_config (patient_id, exercise_id, current_rep)
            values (@patient_id, @exercise_id, @suggested_rep);
 
        commit transaction;
    end try
    begin catch
        rollback transaction;
        throw;
    end catch;
end;
go
 
 
-- ghi cuoi buoi tap
-- Goi tu Brain sau khi tinh toan xong.
-- Ghi dong thoi session_log + exercise_adjustment.
-- Neu 1 trong 2 loi → rollback ca 2.
 
create proc sp_save_session
    @patient_id       int,
    @exercise_id      int,
    @session_date     date,
    @prescribed_rep   int,
    @actual_rep       int,
    @pain_count       int,
    @suggested_rep    int,
    @adjustment_action nvarchar(20),
    @adjustment_note  nvarchar(255)
as
begin
    set nocount on;
 
    declare @session_log_id  int;
    declare @current_rep int;
 
    -- lay cuong do hien tai de luu vao adjustment
    select @current_rep = current_rep
    from current_config
    where patient_id  = @patient_id
      and exercise_id = @exercise_id;
 
    if @current_rep is null
        set @current_rep = @prescribed_rep;
 
    begin transaction;
    begin try
        -- 1. ghi buoi tap
        insert into session_log
            (patient_id, exercise_id, session_date, prescribed_rep, actual_rep, pain_count)
        values
            (@patient_id, @exercise_id, @session_date, @prescribed_rep, @actual_rep, @pain_count);
 
        set @session_log_id = scope_identity();
 
        -- 2. ghi de xuat dieu chinh (chua xac nhan, cho benh nhan duyet)
        insert into exercise_adjustment
            (session_log_id, patient_id, exercise_id, adjustment_date,
             current_rep, suggested_rep, adjustment_action, adjustment_note)
        values
            (@session_log_id, @patient_id, @exercise_id, @session_date,
             @current_rep, @suggested_rep, @adjustment_action, @adjustment_note);
 
        commit transaction;
    end try
    begin catch
        rollback transaction;
        throw;
    end catch;
end;
go
 
 
-- data mau
 
insert into rehab_exercises
    (khop_tap, ten, up_angle, down_angle, diem_a, diem_b, diem_c, huong_dan)
values
-- dau goi
('đầu gối', 'trượt gối',
 '60-90', '160-170', 'hông', 'đầu gối', 'cổ chân',
 'sử dụng đầu gối để co chân. nâng đầu gối lên xuống'),

('đầu gối', 'nâng chân thẳng',
 '30-45', '0-5', 'hông', 'đầu gối', 'cổ chân',
 'giữ nguyên chân thẳng, nâng toàn bộ chân lên xuống'),

('đầu gối', 'ngồi dựa tường',
 '160-170', '85-95', 'hông', 'đầu gối', 'cổ chân',
 'đứng dựa lưng vào tường. trượt xuống đến góc đầu gối = 90°'),

('đầu gối', 'gập gối đứng',
 '100-130', '40-60', 'hông', 'đầu gối', 'cổ chân',
 'đứng thẳng, giữ tựa tay vào tường. gập một đầu gối, đưa gót về phía mông'),

-- khuyu tay
('khuỷu tay', 'gập/duỗi khuỷu tay',
 '30-45', '160-175', 'vai', 'khuỷu tay', 'cổ tay',
 'gập khuỷu tay. tay để trên mặt phẳng'),

('khuỷu tay', 'duỗi tay trên đầu',
 '160-170', '40-60', 'vai', 'khuỷu tay', 'cổ tay',
 'cánh tay dựng thẳng đứng. gập khuỷu ra sau đầu'),

('khuỷu tay', 'gập cánh tay đứng',
 '30-50','160-175', 'vai', 'khuỷu tay', 'cổ tay',
 'gập khuỷu tay. duỗi hoàn toàn cánh tay'),

('khuỷu tay', 'duỗi khuỷu nhờ trọng lực',
 '35-55', '160-175', 'vai', 'khuỷu tay', 'cổ tay',
 'giữ nguyên cánh tay lơ lửng trên không');

go