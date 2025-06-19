-- Simple migration script for authentication enhancements
-- Run these commands one by one in Supabase SQL editor

-- Step 1: Add user_id column to profiles table
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS user_id UUID DEFAULT uuid_generate_v4() UNIQUE;

-- Step 2: Make email unique in profiles table
ALTER TABLE profiles ADD CONSTRAINT profiles_email_key UNIQUE (email);

-- Step 3: Create users_forgot_password table
CREATE TABLE users_forgot_password (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id UUID REFERENCES profiles(user_id) ON DELETE CASCADE,
  email TEXT NOT NULL,
  reset_token TEXT NOT NULL UNIQUE,
  token_expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
  is_used BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  used_at TIMESTAMP WITH TIME ZONE
);

-- Step 4: Create indexes
CREATE INDEX idx_users_forgot_password_token ON users_forgot_password(reset_token);
CREATE INDEX idx_users_forgot_password_email ON users_forgot_password(email);
CREATE INDEX idx_users_forgot_password_user_id ON users_forgot_password(user_id);

-- Step 5: Enable RLS
ALTER TABLE users_forgot_password ENABLE ROW LEVEL SECURITY;

-- Step 6: Create RLS policies
CREATE POLICY "Users can view their own forgot password records" ON users_forgot_password
    FOR SELECT USING (user_id = (SELECT user_id FROM profiles WHERE id = auth.uid()));

CREATE POLICY "Users can insert their own forgot password records" ON users_forgot_password
    FOR INSERT WITH CHECK (user_id = (SELECT user_id FROM profiles WHERE id = auth.uid()));

CREATE POLICY "Users can update their own forgot password records" ON users_forgot_password
    FOR UPDATE USING (user_id = (SELECT user_id FROM profiles WHERE id = auth.uid()));

-- Step 7: Grant permissions
GRANT SELECT, INSERT, UPDATE ON users_forgot_password TO authenticated;

-- Step 8: Create cleanup function
CREATE OR REPLACE FUNCTION cleanup_expired_reset_tokens()
RETURNS void AS $$
BEGIN
    DELETE FROM users_forgot_password 
    WHERE token_expires_at < NOW() - INTERVAL '7 days';
END;
$$ LANGUAGE plpgsql;

-- Step 9: Create trigger function for updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Step 10: Apply trigger to profiles table
DROP TRIGGER IF EXISTS update_profiles_updated_at ON profiles;
CREATE TRIGGER update_profiles_updated_at
    BEFORE UPDATE ON profiles
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();