-- 10_dates_as_strings.sql
-- Expected: WARNING wrong_data_type for VARCHAR used on date-like columns
-- Triggers: created, updated, date, time, timestamp columns as VARCHAR/TEXT

CREATE TABLE events (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(200) NOT NULL,
    created VARCHAR(50),
    updated VARCHAR(50),
    started VARCHAR(50),
    ended VARCHAR(50),
    scheduled TEXT,
    published VARCHAR(30),
    expired VARCHAR(30),
    event_date VARCHAR(10),
    event_time TEXT
);
