-- Users Table
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    phone_number VARCHAR UNIQUE,
    name VARCHAR,
    timezone VARCHAR DEFAULT 'Asia/Jakarta',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Attributes Table
CREATE TABLE IF NOT EXISTS attributes (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    key VARCHAR,
    value VARCHAR,
    unit VARCHAR,
    notes TEXT,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Attribute History Table
CREATE TABLE IF NOT EXISTS attribute_history (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    key VARCHAR,
    value VARCHAR,
    unit VARCHAR,
    notes TEXT,
    recorded_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- TimeLogs Table
CREATE TABLE IF NOT EXISTS timelogs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    activity VARCHAR,
    start_time TIMESTAMP WITH TIME ZONE,
    end_time TIMESTAMP WITH TIME ZONE,
    duration_minutes INTEGER,
    category VARCHAR
);

-- FuturePlans Table
CREATE TABLE IF NOT EXISTS future_plans (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    activity VARCHAR,
    planned_start TIMESTAMP WITH TIME ZONE,
    planned_end TIMESTAMP WITH TIME ZONE,
    status VARCHAR DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
