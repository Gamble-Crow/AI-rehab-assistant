create table exercises(
	exerciseId nvarchar(50) primary key,
	exerciseName nvarchar(100) not null,
	bodyPart nvarchar(50),
	point1 int not null,
	point2 int not null,
	point3 int not null,
	minangle float,
	maxangle float,
	isIsometric bit default 0,
	holdtime int default 0
);

create table patient(
	patientId int identity(1, 1) primary key,
	fullname nvarchar (100) not null,
	dob date,
	condition nvarchar(max)
);

create table workoutlogs(
	logId int identity (1, 1) primary key,
	patientId int references patient(patientId),
	exerciseId nvarchar(50) references exercises(exerciseId),
	painLevel int check (painLevel between 1 and 10),
	reps int default 0,
	hold int default 0,
	maxanglereach float,
	sessiondate datetime default getdate()
);