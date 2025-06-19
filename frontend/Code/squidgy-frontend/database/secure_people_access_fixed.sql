-- Fixed Secure People Access - Only show people with accepted invitations
-- This version is more restrictive and only shows actual invitation connections

-- Drop the existing view first
DROP VIEW IF EXISTS user_connections;

-- Create a much simpler and more secure view
CREATE OR REPLACE VIEW user_connections AS
WITH current_user_info AS (
  SELECT user_id FROM profiles WHERE id = auth.uid()
),
invited_connections AS (
  -- People you've invited who accepted
  SELECT DISTINCT i.recipient_id as connected_user_id
  FROM invitations i, current_user_info cui
  WHERE i.sender_id = cui.user_id
    AND i.status = 'accepted'
    AND i.recipient_id IS NOT NULL
  
  UNION
  
  -- People who invited you and you accepted
  SELECT DISTINCT i.sender_id as connected_user_id
  FROM invitations i, current_user_info cui  
  WHERE i.recipient_id = cui.user_id
    AND i.status = 'accepted'
    AND i.sender_id IS NOT NULL
)
SELECT DISTINCT
  p.id,
  p.user_id,
  p.email,
  p.full_name,
  p.avatar_url,
  p.role,
  p.created_at,
  p.updated_at,
  p.company_id,
  cui.user_id as viewer_user_id,
  'invited' as connection_type
FROM profiles p
CROSS JOIN current_user_info cui
INNER JOIN invited_connections ic ON p.user_id = ic.connected_user_id
WHERE p.user_id != cui.user_id;

-- Grant permissions
GRANT SELECT ON user_connections TO authenticated;

-- Create a simple function that just returns the view results
CREATE OR REPLACE FUNCTION get_connected_people()
RETURNS TABLE (
  id uuid,
  user_id uuid,
  email text,
  full_name text,
  avatar_url text,
  role text,
  created_at timestamptz,
  updated_at timestamptz,
  company_id uuid,
  connection_type text
)
LANGUAGE sql
SECURITY DEFINER
AS $$
  SELECT 
    uc.id,
    uc.user_id,
    uc.email,
    uc.full_name,
    uc.avatar_url,
    uc.role,
    uc.created_at,
    uc.updated_at,
    uc.company_id,
    uc.connection_type
  FROM user_connections uc
  ORDER BY uc.full_name;
$$;

-- Grant execute permission
GRANT EXECUTE ON FUNCTION get_connected_people TO authenticated;

-- Test queries to verify it works
-- Check current user
SELECT 'Current user info' as test, user_id, email, full_name FROM profiles WHERE id = auth.uid();

-- Check invitations
SELECT 'Your sent invitations' as test, recipient_email, status FROM invitations 
WHERE sender_id = (SELECT user_id FROM profiles WHERE id = auth.uid());

SELECT 'Your received invitations' as test, recipient_email, status FROM invitations 
WHERE recipient_id = (SELECT user_id FROM profiles WHERE id = auth.uid());

-- Check connected people (should be empty if no accepted invitations)
SELECT 'Connected people' as test, COUNT(*) as count FROM user_connections;
SELECT * FROM user_connections LIMIT 5;