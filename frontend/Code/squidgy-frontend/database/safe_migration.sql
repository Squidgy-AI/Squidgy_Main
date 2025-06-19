-- Safe migration script for authentication enhancements
-- This script checks for existing objects before creating them

-- Step 1: Add user_id column to profiles table (only if it doesn't exist)
DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name = 'profiles' AND column_name = 'user_id') THEN
        ALTER TABLE profiles ADD COLUMN user_id UUID DEFAULT uuid_generate_v4() UNIQUE;
        RAISE NOTICE 'Added user_id column to profiles table';
    ELSE
        RAISE NOTICE 'user_id column already exists in profiles table';
    END IF;
END $$;

-- Step 2: Make email unique in profiles table (only if constraint doesn't exist)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints 
                   WHERE table_name = 'profiles' AND constraint_name = 'profiles_email_key') THEN
        ALTER TABLE profiles ADD CONSTRAINT profiles_email_key UNIQUE (email);
        RAISE NOTICE 'Added email unique constraint to profiles table';
    ELSE
        RAISE NOTICE 'Email unique constraint already exists on profiles table';
    END IF;
END $$;

-- Step 3: Create users_forgot_password table (only if it doesn't exist)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables 
                   WHERE table_name = 'users_forgot_password') THEN
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
        RAISE NOTICE 'Created users_forgot_password table';
    ELSE
        RAISE NOTICE 'users_forgot_password table already exists';
    END IF;
END $$;

-- Step 4: Create indexes (only if they don't exist)
CREATE INDEX IF NOT EXISTS idx_users_forgot_password_token ON users_forgot_password(reset_token);
CREATE INDEX IF NOT EXISTS idx_users_forgot_password_email ON users_forgot_password(email);
CREATE INDEX IF NOT EXISTS idx_users_forgot_password_user_id ON users_forgot_password(user_id);

-- Step 5: Enable RLS on users_forgot_password table
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables 
               WHERE table_name = 'users_forgot_password') THEN
        ALTER TABLE users_forgot_password ENABLE ROW LEVEL SECURITY;
        RAISE NOTICE 'Enabled RLS on users_forgot_password table';
    END IF;
END $$;

-- Step 6: Create RLS policies (drop and recreate to avoid conflicts)
DROP POLICY IF EXISTS "Users can view their own forgot password records" ON users_forgot_password;
DROP POLICY IF EXISTS "Users can insert their own forgot password records" ON users_forgot_password;
DROP POLICY IF EXISTS "Users can update their own forgot password records" ON users_forgot_password;

-- Create the policies
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables 
               WHERE table_name = 'users_forgot_password') THEN
        
        CREATE POLICY "Users can view their own forgot password records" ON users_forgot_password
            FOR SELECT USING (user_id = (SELECT user_id FROM profiles WHERE id = auth.uid()));

        CREATE POLICY "Users can insert their own forgot password records" ON users_forgot_password
            FOR INSERT WITH CHECK (user_id = (SELECT user_id FROM profiles WHERE id = auth.uid()));

        CREATE POLICY "Users can update their own forgot password records" ON users_forgot_password
            FOR UPDATE USING (user_id = (SELECT user_id FROM profiles WHERE id = auth.uid()));
            
        RAISE NOTICE 'Created RLS policies for users_forgot_password table';
    END IF;
END $$;

-- Step 7: Grant permissions
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables 
               WHERE table_name = 'users_forgot_password') THEN
        GRANT SELECT, INSERT, UPDATE ON users_forgot_password TO authenticated;
        RAISE NOTICE 'Granted permissions on users_forgot_password table';
    END IF;
END $$;

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

-- Step 10: Apply trigger to profiles table (drop first to avoid conflicts)
DROP TRIGGER IF EXISTS update_profiles_updated_at ON profiles;
CREATE TRIGGER update_profiles_updated_at
    BEFORE UPDATE ON profiles
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Final verification
DO $$
BEGIN
    RAISE NOTICE '=== Migration Summary ===';
    
    -- Check user_id column
    IF EXISTS (SELECT 1 FROM information_schema.columns 
               WHERE table_name = 'profiles' AND column_name = 'user_id') THEN
        RAISE NOTICE '✓ user_id column exists in profiles table';
    ELSE
        RAISE NOTICE '✗ user_id column missing from profiles table';
    END IF;
    
    -- Check email constraint
    IF EXISTS (SELECT 1 FROM information_schema.table_constraints 
               WHERE table_name = 'profiles' AND constraint_name = 'profiles_email_key') THEN
        RAISE NOTICE '✓ Email unique constraint exists on profiles table';
    ELSE
        RAISE NOTICE '✗ Email unique constraint missing from profiles table';
    END IF;
    
    -- Check forgot password table
    IF EXISTS (SELECT 1 FROM information_schema.tables 
               WHERE table_name = 'users_forgot_password') THEN
        RAISE NOTICE '✓ users_forgot_password table exists';
    ELSE
        RAISE NOTICE '✗ users_forgot_password table missing';
    END IF;
    
    RAISE NOTICE '=== Migration Complete ===';
END $$;