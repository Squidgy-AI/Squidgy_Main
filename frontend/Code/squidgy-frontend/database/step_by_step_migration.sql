-- Step-by-Step Invitations Migration
-- Run these commands ONE BY ONE in Supabase SQL Editor

-- STEP 1: Check current state
SELECT 'Current invitations count' as info, COUNT(*) as count FROM invitations;
SELECT 'Current profiles count' as info, COUNT(*) as count FROM profiles;

-- STEP 2: Drop foreign key constraints (safe to run multiple times)
ALTER TABLE invitations DROP CONSTRAINT IF EXISTS invitations_sender_id_fkey;
ALTER TABLE invitations DROP CONSTRAINT IF EXISTS invitations_recipient_id_fkey;

-- STEP 3: Check which profiles have user_id column
SELECT 'Profiles with user_id' as info, COUNT(*) as count 
FROM profiles 
WHERE user_id IS NOT NULL;

-- STEP 4: Show sample of current invitation data
SELECT id, sender_id, recipient_id, recipient_email, status 
FROM invitations 
LIMIT 5;

-- STEP 5: Update sender_id references (run this carefully)
UPDATE invitations 
SET sender_id = (
  SELECT user_id 
  FROM profiles 
  WHERE profiles.id = invitations.sender_id::uuid
) 
WHERE sender_id IS NOT NULL 
AND EXISTS (
  SELECT 1 FROM profiles WHERE profiles.id = invitations.sender_id::uuid
);

-- STEP 6: Check how many were updated
SELECT 'Invitations after sender_id update' as info, 
       COUNT(*) as total,
       COUNT(sender_id) as with_sender_id
FROM invitations;

-- STEP 7: Update recipient_id references  
UPDATE invitations 
SET recipient_id = (
  SELECT user_id 
  FROM profiles 
  WHERE profiles.id = invitations.recipient_id::uuid
) 
WHERE recipient_id IS NOT NULL 
AND EXISTS (
  SELECT 1 FROM profiles WHERE profiles.id = invitations.recipient_id::uuid
);

-- STEP 8: Check results
SELECT 'Invitations after recipient_id update' as info,
       COUNT(*) as total,
       COUNT(recipient_id) as with_recipient_id  
FROM invitations;

-- STEP 9: Clean up invalid sender references
UPDATE invitations 
SET sender_id = NULL 
WHERE sender_id IS NOT NULL 
AND NOT EXISTS (
  SELECT 1 FROM profiles WHERE profiles.user_id = invitations.sender_id::uuid
);

-- STEP 10: Remove completely invalid invitations
DELETE FROM invitations 
WHERE recipient_id IS NOT NULL 
AND NOT EXISTS (
  SELECT 1 FROM profiles WHERE profiles.user_id = invitations.recipient_id::uuid
);

-- STEP 11: Verify data integrity before adding constraints
SELECT 'Final verification' as step;

-- Check for orphaned references (should be 0)
SELECT 'Orphaned senders' as check, COUNT(*) as count
FROM invitations i
LEFT JOIN profiles p ON p.user_id = i.sender_id::uuid
WHERE i.sender_id IS NOT NULL AND p.user_id IS NULL;

SELECT 'Orphaned recipients' as check, COUNT(*) as count
FROM invitations i  
LEFT JOIN profiles p ON p.user_id = i.recipient_id::uuid
WHERE i.recipient_id IS NOT NULL AND p.user_id IS NULL;

-- STEP 12: Only run this if verification shows 0 orphaned records
-- Add foreign key constraints
ALTER TABLE invitations 
ADD CONSTRAINT invitations_sender_id_fkey 
FOREIGN KEY (sender_id) REFERENCES profiles(user_id) ON DELETE SET NULL;

-- STEP 13: Add recipient constraint
ALTER TABLE invitations 
ADD CONSTRAINT invitations_recipient_id_fkey 
FOREIGN KEY (recipient_id) REFERENCES profiles(user_id) ON DELETE CASCADE;

-- STEP 14: Create indexes
CREATE INDEX IF NOT EXISTS idx_invitations_sender_id ON invitations(sender_id);
CREATE INDEX IF NOT EXISTS idx_invitations_recipient_id ON invitations(recipient_id);

-- STEP 15: Update RLS policies
DROP POLICY IF EXISTS "Users can view invitations they sent" ON invitations;
DROP POLICY IF EXISTS "Users can view invitations they received" ON invitations;  
DROP POLICY IF EXISTS "Users can update invitations they received" ON invitations;
DROP POLICY IF EXISTS "Users can insert invitations" ON invitations;

CREATE POLICY "Users can view invitations they sent" ON invitations
    FOR SELECT USING (sender_id = (SELECT user_id FROM profiles WHERE id = auth.uid()));

CREATE POLICY "Users can view invitations they received" ON invitations  
    FOR SELECT USING (recipient_id = (SELECT user_id FROM profiles WHERE id = auth.uid()));

CREATE POLICY "Users can update invitations they received" ON invitations
    FOR UPDATE USING (recipient_id = (SELECT user_id FROM profiles WHERE id = auth.uid()));

CREATE POLICY "Users can insert invitations" ON invitations
    FOR INSERT WITH CHECK (sender_id = (SELECT user_id FROM profiles WHERE id = auth.uid()));

-- STEP 16: Final verification
SELECT 'Migration complete' as status;
SELECT 'Total invitations' as info, COUNT(*) as count FROM invitations;
SELECT 'Foreign key constraints' as check, COUNT(*) as count
FROM information_schema.table_constraints 
WHERE table_name = 'invitations' AND constraint_type = 'FOREIGN KEY';