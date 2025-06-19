-- Secure People Access Implementation
-- Create views and policies to ensure users only see people they're connected to

-- 1. Create a view for connected people (people you can see)
CREATE OR REPLACE VIEW user_connections AS
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
  current_user_profile.user_id as viewer_user_id,
  'invited' as connection_type
FROM profiles p
CROSS JOIN (
  SELECT user_id FROM profiles WHERE id = auth.uid()
) as current_user_profile
WHERE p.user_id IN (
  -- People you've invited (as sender)
  SELECT DISTINCT i.recipient_id
  FROM invitations i
  WHERE i.sender_id = current_user_profile.user_id
    AND i.status = 'accepted'
  
  UNION
  
  -- People who invited you (as recipient) 
  SELECT DISTINCT i.sender_id
  FROM invitations i
  WHERE i.recipient_id = current_user_profile.user_id
    AND i.status = 'accepted'
    
  UNION
  
  -- People in the same groups
  SELECT DISTINCT gm.user_id
  FROM group_members gm
  WHERE gm.group_id IN (
    SELECT DISTINCT gm2.group_id
    FROM group_members gm2
    WHERE gm2.user_id = current_user_profile.user_id
  )
  AND gm.user_id != current_user_profile.user_id
  
  UNION
  
  -- People in the same company
  SELECT DISTINCT p2.user_id
  FROM profiles p2
  CROSS JOIN profiles current_profile
  WHERE current_profile.id = auth.uid()
    AND p2.company_id = current_profile.company_id
    AND p2.company_id IS NOT NULL
    AND p2.user_id != current_profile.user_id
)
AND p.user_id != current_user_profile.user_id;

-- 2. Grant permissions on the view
GRANT SELECT ON user_connections TO authenticated;

-- 3. Create RLS policy for the view (extra security)
ALTER VIEW user_connections SET (security_barrier = true);

-- 4. Create function to get connected people for a user
CREATE OR REPLACE FUNCTION get_connected_people(target_user_id uuid DEFAULT NULL)
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
  WHERE (target_user_id IS NULL OR uc.viewer_user_id = target_user_id)
  ORDER BY uc.full_name;
$$;

-- 5. Grant execute permission on the function
GRANT EXECUTE ON FUNCTION get_connected_people TO authenticated;

-- 6. Test the function
-- SELECT * FROM get_connected_people() LIMIT 5;