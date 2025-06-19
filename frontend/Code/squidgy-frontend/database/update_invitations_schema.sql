-- Update invitations table schema to use user_id references
-- This script updates the foreign key constraints to reference profiles.user_id

-- Step 1: Drop existing foreign key constraints
ALTER TABLE invitations DROP CONSTRAINT IF EXISTS invitations_sender_id_fkey;
ALTER TABLE invitations DROP CONSTRAINT IF EXISTS invitations_recipient_id_fkey;

-- Step 2: Add new foreign key constraints referencing user_id
ALTER TABLE invitations 
ADD CONSTRAINT invitations_sender_id_fkey 
FOREIGN KEY (sender_id) REFERENCES profiles(user_id) ON DELETE SET NULL;

ALTER TABLE invitations 
ADD CONSTRAINT invitations_recipient_id_fkey 
FOREIGN KEY (recipient_id) REFERENCES profiles(user_id) ON DELETE CASCADE;

-- Step 3: Update any existing data (if needed)
-- This step ensures any existing invitations reference the correct user_id
UPDATE invitations 
SET sender_id = (
  SELECT user_id 
  FROM profiles 
  WHERE profiles.id = invitations.sender_id
) 
WHERE sender_id IS NOT NULL 
AND EXISTS (
  SELECT 1 FROM profiles WHERE profiles.id = invitations.sender_id
);

UPDATE invitations 
SET recipient_id = (
  SELECT user_id 
  FROM profiles 
  WHERE profiles.id = invitations.recipient_id
) 
WHERE recipient_id IS NOT NULL 
AND EXISTS (
  SELECT 1 FROM profiles WHERE profiles.id = invitations.recipient_id
);

-- Step 4: Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_invitations_sender_id ON invitations(sender_id);
CREATE INDEX IF NOT EXISTS idx_invitations_recipient_id ON invitations(recipient_id);
CREATE INDEX IF NOT EXISTS idx_invitations_recipient_email ON invitations(recipient_email);
CREATE INDEX IF NOT EXISTS idx_invitations_status ON invitations(status);
CREATE INDEX IF NOT EXISTS idx_invitations_token ON invitations(token);

-- Step 5: Update RLS policies if they exist
DROP POLICY IF EXISTS "Users can view invitations they sent" ON invitations;
DROP POLICY IF EXISTS "Users can view invitations they received" ON invitations;
DROP POLICY IF EXISTS "Users can update invitations they received" ON invitations;

-- Create new RLS policies using user_id
CREATE POLICY "Users can view invitations they sent" ON invitations
    FOR SELECT USING (sender_id = (SELECT user_id FROM profiles WHERE id = auth.uid()));

CREATE POLICY "Users can view invitations they received" ON invitations
    FOR SELECT USING (recipient_id = (SELECT user_id FROM profiles WHERE id = auth.uid()));

CREATE POLICY "Users can update invitations they received" ON invitations
    FOR UPDATE USING (recipient_id = (SELECT user_id FROM profiles WHERE id = auth.uid()));

CREATE POLICY "Users can insert invitations" ON invitations
    FOR INSERT WITH CHECK (sender_id = (SELECT user_id FROM profiles WHERE id = auth.uid()));

-- Step 6: Grant necessary permissions
GRANT SELECT, INSERT, UPDATE ON invitations TO authenticated;