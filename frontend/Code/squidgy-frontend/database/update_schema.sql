-- Update schema for enhanced authentication
-- This script adds user_id to profiles table and creates forgot password table

-- 1. Add user_id column to profiles table (if it doesn't exist)
DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name = 'profiles' AND column_name = 'user_id') THEN
        ALTER TABLE profiles ADD COLUMN user_id UUID DEFAULT uuid_generate_v4() UNIQUE NOT NULL;
        COMMENT ON COLUMN profiles.user_id IS 'Widely used user identifier for API consistency';
    END IF;
END $$;

-- 2. Make email unique in profiles table (if not already)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints 
                   WHERE table_name = 'profiles' AND constraint_name = 'profiles_email_key') THEN
        ALTER TABLE profiles ADD CONSTRAINT profiles_email_key UNIQUE (email);
    END IF;
END $$;

-- 3. Create users_forgot_password table (if it doesn't exist)
CREATE TABLE IF NOT EXISTS users_forgot_password (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id UUID REFERENCES profiles(user_id) ON DELETE CASCADE,
  email TEXT NOT NULL,
  reset_token TEXT NOT NULL UNIQUE,
  token_expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
  is_used BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  used_at TIMESTAMP WITH TIME ZONE
);

-- 4. Create index on reset_token for faster lookups
CREATE INDEX IF NOT EXISTS idx_users_forgot_password_token ON users_forgot_password(reset_token);
CREATE INDEX IF NOT EXISTS idx_users_forgot_password_email ON users_forgot_password(email);
CREATE INDEX IF NOT EXISTS idx_users_forgot_password_user_id ON users_forgot_password(user_id);

-- 5. Enable RLS on users_forgot_password table
ALTER TABLE users_forgot_password ENABLE ROW LEVEL SECURITY;

-- 6. Create RLS policies for users_forgot_password
CREATE POLICY "Users can view their own forgot password records" ON users_forgot_password
    FOR SELECT USING (user_id = (SELECT user_id FROM profiles WHERE id = auth.uid()));

CREATE POLICY "Users can insert their own forgot password records" ON users_forgot_password
    FOR INSERT WITH CHECK (user_id = (SELECT user_id FROM profiles WHERE id = auth.uid()));

CREATE POLICY "Users can update their own forgot password records" ON users_forgot_password
    FOR UPDATE USING (user_id = (SELECT user_id FROM profiles WHERE id = auth.uid()));

-- 7. Create function to cleanup expired reset tokens (optional - runs daily)
CREATE OR REPLACE FUNCTION cleanup_expired_reset_tokens()
RETURNS void AS $$
BEGIN
    DELETE FROM users_forgot_password 
    WHERE token_expires_at < NOW() - INTERVAL '7 days';
END;
$$ LANGUAGE plpgsql;

-- 8. Create trigger to automatically update profiles.updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply the trigger to profiles table (if not exists)
DROP TRIGGER IF EXISTS update_profiles_updated_at ON profiles;
CREATE TRIGGER update_profiles_updated_at
    BEFORE UPDATE ON profiles
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- 9. Grant necessary permissions
GRANT SELECT, INSERT, UPDATE ON users_forgot_password TO authenticated;