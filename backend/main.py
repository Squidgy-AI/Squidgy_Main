# main.py - Complete integration with conversational handler and vector search agent matching

# Standard library imports
import asyncio
import json
import logging
import os
import time
import uuid
from collections import deque
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional, AsyncGenerator, List, Tuple, Set

# Third-party imports
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import OpenAI
from pydantic import BaseModel
from supabase import create_client, Client

# Local imports
from agent_config import get_agent_config, AGENTS
from Website.web_scrape import capture_website_screenshot_async, get_website_favicon_async

# Handler classes

class AgentMatcher:
    def __init__(self, supabase_client, openai_api_key: str = None):
        self.supabase = supabase_client
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        self.openai_client = OpenAI(api_key=self.openai_api_key)
        self._cache = {}  # Cache for agent matching results
        self._cache_ttl = 300  # Cache TTL in seconds (5 minutes)

    async def get_query_embedding(self, text: str) -> List[float]:
        """Generate embedding for the query text using OpenAI"""
        try:
            response = self.openai_client.embeddings.create(
                model="text-embedding-3-small",
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Error generating embedding: {str(e)}")
            raise

    async def check_agent_match(self, agent_name: str, user_query: str, threshold: float = 0.3) -> tuple:
        """Check if a specific agent matches the user query using vector similarity"""
        try:
            # Skip check if agent doesn't exist
            agent_check = self.supabase.table('agent_documents')\
                .select('id')\
                .eq('agent_name', agent_name)\
                .limit(1)\
                .execute()
            
            if not agent_check.data:
                logger.warning(f"No documents found for agent '{agent_name}' in database")
                return False

            # Get cached result if exists
            cache_key = f"agent_match_{agent_name}_{user_query}"
            cached = self._cache.get(cache_key)
            if cached and (datetime.now() - cached['timestamp']).total_seconds() < self._cache_ttl:
                return cached['result']

            # Always perform vector search first to get similarity score
            query_embedding = await self.get_query_embedding(user_query)
            
            result = self.supabase.rpc(
                'match_agent_documents',
                {
                    'query_embedding': query_embedding,
                    'match_threshold': threshold,
                    'match_count': 1,
                    'filter_agent': agent_name
                }
            ).execute()
            
            # Debug logging
            if result.data:
                logger.debug(f"Vector search result: {len(result.data)} matches found")
                if len(result.data) > 0:
                    logger.debug(f"Best match similarity: {result.data[0]['similarity']:.3f} (threshold: {threshold})")
            else:
                logger.debug(f"Vector search result: No matches found for agent '{agent_name}'")

            # Check if this is a basic greeting or general question that any agent can handle
            basic_patterns = [
                'hello', 'hi', 'hey', 'good morning', 'good afternoon', 'good evening',
                'who are you', 'what are you', 'introduce yourself', 'tell me about yourself',
                'what do you do', 'what can you help with', 'how can you help', 'what services',
                'greetings', 'salutations', 'yo', 'howdy', 'how are you', 'how do you do',
                'nice to meet you', 'pleased to meet you', 'what is this', 'explain this',
                'help', 'assistance', 'support', 'info', 'information', 'thanks', 'thank you'
            ]
            
            query_lower = user_query.lower().strip()
            is_basic = any(pattern in query_lower for pattern in basic_patterns) or len(query_lower.split()) <= 3
            
            # Calculate confidence score
            confidence = result.data[0]['similarity'] if result.data else 0.0
            
            # For basic queries, always return True regardless of similarity score
            # But set a minimum confidence just above threshold (0.4) for basic queries
            if is_basic:
                logger.debug(f"Basic query detected: Any agent (including {agent_name}) can handle: '{user_query}'")
                match_result = True
                # Basic queries should have reasonable confidence above threshold
                if confidence < 0.4:
                    confidence = 0.4
            else:
                # For non-basic queries, use the similarity threshold
                match_result = len(result.data) > 0 and result.data[0]['similarity'] >= threshold
            
            # Cache the result with confidence
            self._cache[cache_key] = {
                'result': (match_result, confidence),
                'timestamp': datetime.now()
            }

            # Only log for real queries
            if user_query and user_query.strip():
                if match_result:
                    logger.debug(f"Agent match SUCCESS: {agent_name} is appropriate for this query (confidence: {confidence:.3f})")
                else:
                    logger.debug(f"Agent match FAILED: {agent_name} - checking for better alternatives...")
            return match_result, confidence
            
        except Exception as e:
            logger.error(f"Error checking agent match: {str(e)}")
            return False, 0.0

    async def find_best_agents(self, user_query: str, top_n: int = 3) -> List[Tuple[str, float]]:
        """Find the best matching agents for a user query using vector similarity"""
        try:
            # Get cached result if exists
            cache_key = f"best_agents_{user_query}"
            cached = self._cache.get(cache_key)
            if cached and (datetime.now() - cached['timestamp']).total_seconds() < self._cache_ttl:
                return cached['result']

            query_embedding = await self.get_query_embedding(user_query)
            
            result = self.supabase.rpc(
                'match_agents_by_similarity',
                {
                    'query_embedding': query_embedding,
                    'match_threshold': 0.3,
                    'match_count': top_n * 5
                }
            ).execute()
            
            if not result.data:
                return [('presaleskb', 50.0)]
            
            agent_scores = {}
            for item in result.data:
                agent_name = item['agent_name']
                similarity = item['similarity'] * 100
                
                if agent_name not in agent_scores or similarity > agent_scores[agent_name]:
                    agent_scores[agent_name] = similarity
            
            sorted_agents = sorted(agent_scores.items(), key=lambda x: x[1], reverse=True)
            
            # Cache the result
            self._cache[cache_key] = {
                'result': sorted_agents[:top_n],
                'timestamp': datetime.now()
            }
            
            return sorted_agents[:top_n]
            
        except Exception as e:
            logger.error(f"Error finding best agents: {str(e)}")
            return [('presaleskb', 50.0)]

    async def get_recommended_agent(self, user_query: str) -> str:
        """Get the single best recommended agent for a query"""
        try:
            # Get cached result if exists
            cache_key = f"recommended_agent_{user_query}"
            cached = self._cache.get(cache_key)
            if cached and (datetime.now() - cached['timestamp']).total_seconds() < self._cache_ttl:
                return cached['result']

            best_agents = await self.find_best_agents(user_query, top_n=1)
            
            if best_agents and best_agents[0][1] >= 60:
                agent = best_agents[0][0]
            else:
                agent = 'presaleskb'

            # Cache the result
            self._cache[cache_key] = {
                'result': agent,
                'timestamp': datetime.now()
            }
            
            return agent
            
        except Exception as e:
            logger.error(f"Error getting recommended agent: {str(e)}")
            return 'presaleskb'

# Conversational Handler Class
class ConversationalHandler:
    def __init__(self, supabase_client, n8n_url: str = os.getenv('N8N_MAIN', 'https://n8n.theaiteam.uk/webhook/c2fcbad6-abc0-43af-8aa8-d1661ff4461d')):
        self._cache = {}  # Simple in-memory cache
        self._cache_ttl = 300  # Cache TTL in seconds (5 minutes)
        self.supabase = supabase_client
        self.n8n_url = n8n_url

    async def get_cached_response(self, request_id: str):
        """Get cached response if it exists and is not expired"""
        cache_key = f"response_{request_id}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if (datetime.now() - cached['timestamp']).total_seconds() < self._cache_ttl:
                return cached['response']
            del self._cache[cache_key]
        return None

    async def cache_response(self, request_id: str, response: dict):
        """Cache a response with TTL"""
        cache_key = f"response_{request_id}"
        self._cache[cache_key] = {
            'response': response,
            'timestamp': datetime.now()
        }

    async def save_to_history(self, session_id: str, user_id: str, user_message: str, agent_response: str):
        """Save message to chat history"""
        try:
            entry = {
                'session_id': session_id,
                'user_id': user_id,
                'user_message': user_message,
                'agent_response': agent_response,
                'timestamp': datetime.now().isoformat()
            }
            
            result = self.supabase.table('chat_history')\
                .insert(entry)\
                .execute()
            
            return result.data[0] if result.data else None
            
        except Exception as e:
            logger.error(f"Error saving to history: {str(e)}")
            return None

    async def handle_message(self, request_data: dict):
        """Handle incoming message with conversational logic"""
        try:
            user_mssg = request_data.get('user_mssg', '')
            session_id = request_data.get('session_id', '')
            user_id = request_data.get('user_id', '')
            agent_name = request_data.get('agent_name', 'presaleskb')
            request_id = request_data.get('request_id', str(uuid.uuid4()))

            # Skip empty messages
            if not user_mssg.strip():
                return {
                    'status': 'error',
                    'message': 'Empty message received'
                }

            # Check cache first
            cached_response = await self.get_cached_response(request_id)
            if cached_response:
                return cached_response

            # Process the message
            response = await self.process_message(user_mssg, session_id, user_id, agent_name)

            # Cache the response
            await self.cache_response(request_id, response)

            return response

        except Exception as e:
            logger.error(f"Error handling message: {str(e)}")
            raise

    async def process_message(self, user_mssg: str, session_id: str, user_id: str, agent_name: str):
        """Process the actual message and get response from n8n"""
        try:
            # Prepare payload for n8n
            payload = {
                'user_id': user_id,
                'user_mssg': user_mssg,
                'session_id': session_id,
                'agent_name': agent_name,
                '_original_message': user_mssg  # Store original message for history
            }

            # Call n8n webhook
            async with httpx.AsyncClient(timeout=None) as client:
                response = await client.post(self.n8n_url, json=payload)
                response.raise_for_status()
                n8n_response = response.json()
                
                # Log the full N8N response for testing
                logger.debug(f"N8N Response: {json.dumps(n8n_response, indent=2)}")

            # Format response
            formatted_response = {
                'status': n8n_response.get('status', 'success'),
                'agent_name': n8n_response.get('agent_name', agent_name),
                'agent_response': n8n_response.get('agent_response', ''),
                'conversation_state': n8n_response.get('conversation_state', 'complete'),
                'missing_info': n8n_response.get('missing_info', []),
                'timestamp': datetime.now().isoformat()
            }

            return formatted_response

        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            return {
                'status': 'error',
                'message': str(e)
            }

# Client KB Manager Class
class ClientKBManager:
    def __init__(self, supabase_client):
        self.supabase = supabase_client
        
    async def update_client_kb(self, user_id: str, query: str, agent_name: str):
        """Update client's knowledge base with new query"""
        try:
            # Use upsert for more efficient database operation
            entry = {
                'user_id': user_id,
                'agent_name': agent_name,
                'query': query,
                'timestamp': datetime.now().isoformat()
            }
            
            result = self.supabase.table('client_kb')\
                .upsert(entry, on_conflict='user_id,agent_name')\
                .execute()
            
            return result.data[0] if result.data else None
            
        except Exception as e:
            logger.error(f"Error updating client KB: {str(e)}")
            return None

# Dynamic Agent KB Handler Class
class DynamicAgentKBHandler:
    def __init__(self, supabase_client):
        self.supabase = supabase_client
        
    async def update_agent_kb(self, agent_name: str, query: str, user_id: str):
        """Update agent's knowledge base with new query"""
        try:
            # Use upsert for more efficient database operation
            entry = {
                'agent_name': agent_name,
                'query': query,
                'user_id': user_id,
                'timestamp': datetime.now().isoformat()
            }
            
            result = self.supabase.table('agent_kb')\
                .upsert(entry, on_conflict='agent_name')\
                .execute()
            
            return result.data[0] if result.data else None
            
        except Exception as e:
            logger.error(f"Error updating agent KB: {str(e)}")
            return None


load_dotenv()
# Initialize FastAPI app
app = FastAPI()
logger = logging.getLogger(__name__)
active_connections: Dict[str, WebSocket] = {}
streaming_sessions: Dict[str, Dict[str, Any]] = {}
# Request deduplication cache
request_cache: Dict[str, float] = {}

# Environment variables
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
N8N_MAIN = os.getenv("N8N_MAIN")
N8N_MAIN_TEST = os.getenv("N8N_MAIN_TEST")

N8N_STREAM_TEST = os.getenv("N8N_STREAM_TEST")
N8N_STREAM_TEST_TEST = os.getenv("N8N_STREAM_TEST_TEST")


print(f"Using Supabase URL: {SUPABASE_URL}")

# Initialize Supabase client
def create_supabase_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize handlers
active_requests: Set[str] = set()

# Initialize Supabase client  
supabase = create_supabase_client()

# Initialize handlers
agent_matcher = AgentMatcher(supabase_client=supabase)
conversational_handler = ConversationalHandler(
    supabase_client=supabase,
    n8n_url=os.getenv('N8N_MAIN', 'https://n8n.theaiteam.uk/webhook/c2fcbad6-abc0-43af-8aa8-d1661ff4461d')
)
client_kb_manager = ClientKBManager(supabase_client=supabase)
dynamic_agent_kb_handler = DynamicAgentKBHandler(supabase_client=supabase)

print("Application initialized")

background_results = {}
running_tasks: Dict[str, Dict[str, Any]] = {}

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

AGENT_DESCRIPTIONS = {
    agent_name: agent_config.description 
    for agent_name, agent_config in AGENTS.items()
}


# Models
class WebsiteFaviconRequest(BaseModel):
    url: str
    session_id: str
    user_id: str

class N8nMainRequest(BaseModel):
    user_id: str
    user_mssg: str
    session_id: str
    agent_name: str
    timestamp_of_call_made: Optional[str] = None
    request_id: Optional[str] = None

class N8nResponse(BaseModel):
    user_id: str
    agent_name: str
    agent_response: str
    responses: List[Dict[str, Any]]
    timestamp: str
    status: str

class StreamUpdate(BaseModel):
    type: str
    user_id: str
    agent_name: Optional[str] = None
    agent_names: Optional[str] = None
    message: str
    progress: int
    agent_response: Optional[str] = None
    metadata: dict

class ConversationState(Enum):
    INITIAL = "initial"
    COLLECTING_INFO = "collecting_info"
    PROCESSING = "processing"
    COMPLETE = "complete"

class N8nCheckAgentMatchRequest(BaseModel):
    agent_name: str
    user_query: str
    threshold: Optional[float] = 0.3

class N8nFindBestAgentsRequest(BaseModel):
    user_query: str
    top_n: Optional[int] = 3
    min_threshold: Optional[float] = 0.3

class ClientKBCheckRequest(BaseModel):
    user_id: str
    session_id: Optional[str] = None
    force_refresh: Optional[bool] = False

class ClientKBResponse(BaseModel):
    user_id: str
    has_website_info: bool
    website_url: Optional[str] = None
    website_analysis: Optional[Dict[str, Any]] = None
    company_info: Optional[Dict[str, Any]] = None
    kb_status: str
    message: str
    action_required: Optional[str] = None
    last_updated: Optional[str] = None

class AgentKBQueryRequest(BaseModel):
    user_id: str
    user_mssg: str
    agent: str

class AgentKBQueryResponse(BaseModel):
    user_id: str
    agent: str
    response_type: str  # "direct_answer", "needs_tools", "needs_info"
    agent_response: Optional[str] = None
    required_tools: Optional[List[Dict[str, Any]]] = None
    follow_up_questions: Optional[List[str]] = None
    missing_information: Optional[List[Dict[str, Any]]] = None
    confidence_score: float
    kb_context_used: bool
    status: str

class WebsiteAnalysisRequest(BaseModel):
    url: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None

class WebsiteScreenshotRequest(BaseModel):
    url: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None

# Conversational Handler Class
# API Endpoints
@app.get("/")
async def health_check():
    return {"status": "healthy", "message": "Squidgy AI WebSocket Server is running"}

@app.get("/health")
async def health_check_detailed():
    return {
        "status": "healthy",
        "active_connections": len(active_connections),
        "streaming_sessions": len(streaming_sessions)
    }

@app.get("/debug/agent-docs/{agent_name}")
async def debug_agent_docs(agent_name: str):
    """Debug endpoint to check agent documents in database"""
    try:
        docs = supabase.table('agent_documents')\
            .select('id, content')\
            .eq('agent_name', agent_name)\
            .execute()
        
        return {
            "agent_name": agent_name,
            "documents_found": len(docs.data) if docs.data else 0,
            "documents": docs.data[:3] if docs.data else [],  # Show first 3 docs
            "sample_content": docs.data[0]['content'][:200] + "..." if docs.data else None
        }
    except Exception as e:
        return {"error": str(e)}

# Agent matching endpoints
# @app.post("/api/agents/check-match")
# async def check_agent_match_endpoint(agent_name: str, user_query: str):
#     """API endpoint to check if an agent matches a query using vector similarity"""
#     try:
#         is_match = await agent_matcher.check_agent_match(agent_name, user_query)
#         return {
#             "agent_name": agent_name,
#             "user_query": user_query,
#             "is_match": is_match
#         }
#     except Exception as e:
#         logger.error(f"Error checking agent match: {str(e)}")
#         raise HTTPException(status_code=500, detail=str(e))

# @app.post("/api/agents/find-best")
# async def find_best_agents_endpoint(user_query: str, top_n: int = 3):
#     """API endpoint to find best matching agents using vector similarity"""
#     try:
#         best_agents = await agent_matcher.find_best_agents(user_query, top_n)
#         return {
#             "user_query": user_query,
#             "recommendations": [
#                 {
#                     "agent_name": agent_name,
#                     "match_percentage": round(score, 2),
#                     "rank": idx + 1
#                 }
#                 for idx, (agent_name, score) in enumerate(best_agents)
#             ]
#         }
#     except Exception as e:
#         logger.error(f"Error finding best agents: {str(e)}")
#         raise HTTPException(status_code=500, detail=str(e))

# N8N agent matching endpoints
@app.post("/n8n/check_agent_match")
async def n8n_check_agent_match(request: N8nCheckAgentMatchRequest):
    """N8N webhook endpoint to check if a specific agent matches the user query"""
    try:
        is_match, confidence = await agent_matcher.check_agent_match(
            agent_name=request.agent_name,
            user_query=request.user_query,
            threshold=request.threshold
        )
        
        # Debug logging
        logger.debug(f"Agent Match Check - Agent: {request.agent_name}, Query: {request.user_query}, Threshold: {request.threshold}, Result: {is_match}, Confidence: {confidence:.3f}")
        
        if is_match:
            recommendation = f"Agent '{request.agent_name}' is suitable for this query"
        else:
            recommendation = f"Agent '{request.agent_name}' may not be optimal for this query"
        
        return {
            "agent_name": request.agent_name,
            "user_query": request.user_query,
            "is_match": is_match,
            "confidence": round(confidence, 3),
            "threshold_used": request.threshold,
            "recommendation": recommendation,
            "status": "success"
        }
        
    except Exception as e:
        logger.error(f"Error in n8n_check_agent_match: {str(e)}")
        return {
            "agent_name": request.agent_name,
            "user_query": request.user_query,
            "is_match": False,
            "error": str(e),
            "status": "error"
        }

@app.post("/n8n/find_best_agents")
async def n8n_find_best_agents(request: N8nFindBestAgentsRequest):
    """N8N webhook endpoint to find the best matching agents for a user query"""
    try:
        best_agents = await agent_matcher.find_best_agents(
            user_query=request.user_query,
            top_n=request.top_n
        )
        
        if request.min_threshold:
            best_agents = [(name, score) for name, score in best_agents if score >= request.min_threshold * 100]
        
        recommendations = []
        for idx, (agent_name, score) in enumerate(best_agents):
            if score >= 90:
                quality = "Excellent match"
            elif score >= 75:
                quality = "Good match"
            elif score >= 60:
                quality = "Fair match"
            else:
                quality = "Possible match"
            
            # agent_descriptions = {
            #     "presaleskb": "specializes in sales and pricing queries",
            #     "socialmediakb": "handles social media and digital marketing",
            #     "leadgenkb": "general purpose assistant for various queries"
            # }

            description = f"{quality} - {AGENT_DESCRIPTIONS.get(agent_name, 'handles specialized queries')}"

            
            # description = f"{quality} - {agent_descriptions.get(agent_name, 'handles specialized queries')}"
            
            recommendations.append({
                "agent_name": agent_name,
                "match_percentage": round(score, 1),
                "rank": idx + 1,
                "description": description
            })
        
        response = {
            "user_query": request.user_query,
            "recommendations": recommendations,
            "total_agents_found": len(recommendations),
            "status": "success"
        }
        
        if recommendations:
            response["best_agent"] = recommendations[0]["agent_name"]
            response["best_agent_confidence"] = recommendations[0]["match_percentage"]
        else:
            response["best_agent"] = "presaleskb"
            response["best_agent_confidence"] = 100.0
            response["message"] = "No agents found above threshold, using default agent"
        
        return response
        
    except Exception as e:
        logger.error(f"Error in n8n_find_best_agents: {str(e)}")
        return {
            "user_query": request.user_query,
            "best_agent": "",
            "best_agent_confidence": 100.0,
            "recommendations": [],
            "error": str(e),
            "status": "error",
            "message": "Error finding agents, using default"
        }

@app.post("/n8n/analyze_agent_query")
async def n8n_analyze_agent_query(request: Dict[str, Any]):
    """Combined N8N webhook endpoint that performs both agent checking and recommendation"""
    try:
        user_query = request.get("user_query", "")
        current_agent = request.get("current_agent")
        get_recommendations = request.get("get_recommendations", True)
        top_n = request.get("top_n", 3)
        
        response = {
            "user_query": user_query,
            "analysis_timestamp": datetime.now().isoformat(),
            "status": "success"
        }
        
        if current_agent:
            is_match, match_confidence = await agent_matcher.check_agent_match(current_agent, user_query)
            response["current_agent_analysis"] = {
                "agent_name": current_agent,
                "is_suitable": is_match,
                "confidence": round(match_confidence, 3),
                "recommendation": "Keep current agent" if is_match else "Consider switching agents"
            }
        
        if get_recommendations:
            best_agents = await agent_matcher.find_best_agents(user_query, top_n)
            
            response["recommended_agents"] = [
                {
                    "agent_name": name,
                    "confidence": round(score, 1),
                    "rank": idx + 1
                }
                for idx, (name, score) in enumerate(best_agents)
            ]
            
            if best_agents:
                best_agent, best_score = best_agents[0]
                
                if current_agent and current_agent == best_agent:
                    response["routing_decision"] = "keep_current"
                    response["routing_message"] = f"Current agent '{current_agent}' is optimal"
                elif best_score >= 70:
                    response["routing_decision"] = "switch_agent"
                    response["suggested_agent"] = best_agent
                    response["routing_message"] = f"Switch to '{best_agent}' (confidence: {best_score}%)"
                else:
                    response["routing_decision"] = "use_default"
                    response["suggested_agent"] = "presaleskb"
                    response["routing_message"] = "No strong match found, use general agent"
        
        return response
        
    except Exception as e:
        logger.error(f"Error in n8n_analyze_agent_query: {str(e)}")
        return {
            "user_query": request.get("user_query", ""),
            "error": str(e),
            "status": "error",
            "routing_decision": "use_default",
            "suggested_agent": "presaleskb"
        }

@app.get("/n8n/agent_matcher/health")
async def n8n_agent_matcher_health():
    """Health check for agent matching service"""
    try:
        test_result = agent_matcher.supabase.table('agent_documents').select('id').limit(1).execute()
        
        return {
            "service": "agent_matcher",
            "status": "healthy",
            "database": "connected",
            "endpoints": [
                "/n8n/check_agent_match",
                "/n8n/find_best_agents",
                "/n8n/analyze_agent_query"
            ],
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "service": "agent_matcher",
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }

# Client KB endpoints
@app.post("/api/client/check_kb")
async def check_client_kb(request: ClientKBCheckRequest):
    """Check if client has website information in KB and return appropriate response"""
    try:
        kb_data = await client_kb_manager.get_client_kb(request.user_id, 'website_info')
        
        if kb_data and not request.force_refresh:
            content = kb_data.get('content', {})
            website_info = content.get('website_info', {})
            
            return ClientKBResponse(
                user_id=request.user_id,
                has_website_info=True,
                website_url=website_info.get('url'),
                website_analysis=website_info,
                company_info={
                    'name': website_info.get('company_name'),
                    'niche': website_info.get('niche'),
                    'description': website_info.get('description'),
                    'services': website_info.get('services', [])
                },
                kb_status='complete',
                message='Client information found',
                last_updated=kb_data.get('updated_at')
            )
        
        website_result = None
        if request.session_id:
            website_result = supabase.table('website_data')\
                .select('*')\
                .eq('user_id', request.user_id)\
                .eq('session_id', request.session_id)\
                .order('created_at', desc=True)\
                .limit(1)\
                .execute()
        else:
            website_result = supabase.table('website_data')\
                .select('*')\
                .eq('user_id', request.user_id)\
                .order('created_at', desc=True)\
                .limit(1)\
                .execute()
        
        if website_result and website_result.data:
            website_data = website_result.data[0]
            
            chat_history_result = supabase.table('chat_history')\
                .select('*')\
                .eq('user_id', request.user_id)\
                .order('timestamp', desc=False)\
                .execute()
            
            chat_history = chat_history_result.data if chat_history_result else []
            
            kb_content = await client_kb_manager.analyze_and_update_kb(
                request.user_id,
                website_data,
                chat_history
            )
            
            if kb_content:
                website_info = kb_content.get('website_info', {})
                return ClientKBResponse(
                    user_id=request.user_id,
                    has_website_info=True,
                    website_url=website_info.get('url'),
                    website_analysis=website_info,
                    company_info={
                        'name': website_info.get('company_name'),
                        'niche': website_info.get('niche'),
                        'description': website_info.get('description'),
                        'services': website_info.get('services', [])
                    },
                    kb_status='updated',
                    message='Client KB updated with latest information',
                    last_updated=datetime.now().isoformat()
                )
        
        return ClientKBResponse(
            user_id=request.user_id,
            has_website_info=False,
            kb_status='missing_website',
            message='Please provide your website URL to continue',
            action_required='collect_website_url'
        )
        
    except Exception as e:
        logger.error(f"Error checking client KB: {str(e)}")
        return ClientKBResponse(
            user_id=request.user_id,
            has_website_info=False,
            kb_status='error',
            message=f'Error checking client information: {str(e)}',
            action_required='retry'
        )

@app.post("/api/client/update_website")
async def update_client_website(request: Dict[str, Any]):
    """Update client KB when website URL is provided"""
    try:
        user_id = request.get('user_id')
        session_id = request.get('session_id')
        website_url = request.get('website_url')
        
        if not all([user_id, website_url]):
            raise HTTPException(status_code=400, detail="Missing required fields")
        
        analysis = await conversational_handler.analyze_website_with_perplexity(website_url)
        
        website_entry = {
            'user_id': user_id,
            'session_id': session_id or str(uuid.uuid4()),
            'url': website_url,
            'analysis': json.dumps(analysis) if analysis else None,
            'created_at': datetime.now().isoformat()
        }
        
        website_result = supabase.table('website_data')\
            .upsert(website_entry, on_conflict='session_id,url')\
            .execute()
        
        if website_result.data:
            chat_history_result = supabase.table('chat_history')\
                .select('*')\
                .eq('user_id', user_id)\
                .order('timestamp', desc=False)\
                .execute()
            
            chat_history = chat_history_result.data if chat_history_result else []
            
            kb_content = await client_kb_manager.analyze_and_update_kb(
                user_id,
                website_result.data[0],
                chat_history
            )
            
            return {
                'status': 'success',
                'user_id': user_id,
                'website_url': website_url,
                'analysis': analysis,
                'kb_updated': bool(kb_content),
                'message': 'Website information saved and KB updated'
            }
        
    except Exception as e:
        logger.error(f"Error updating client website: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/client/context/{user_id}")
async def get_client_context(user_id: str):
    """Get complete client context for agent use"""
    try:
        kb_results = supabase.table('client_kb')\
            .select('*')\
            .eq('client_id', user_id)\
            .execute()
        
        context = {
            'user_id': user_id,
            'kb_entries': {}
        }
        
        if kb_results.data:
            for entry in kb_results.data:
                context['kb_entries'][entry['kb_type']] = entry['content']
        
        chat_history = supabase.table('chat_history')\
            .select('*')\
            .eq('user_id', user_id)\
            .order('timestamp', desc=False)\
            .limit(50)\
            .execute()
        
        context['recent_conversations'] = chat_history.data if chat_history else []
        
        website_data = supabase.table('website_data')\
            .select('*')\
            .eq('user_id', user_id)\
            .order('created_at', desc=True)\
            .limit(1)\
            .execute()
        
        if website_data and website_data.data:
            context['current_website'] = website_data.data[0]
        
        return context
        
    except Exception as e:
        logger.error(f"Error getting client context: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/n8n/check_client_kb")
async def n8n_check_client_kb(request: Dict[str, Any]):
    """N8N-specific endpoint for checking client KB"""
    try:
        kb_request = ClientKBCheckRequest(
            user_id=request.get('user_id'),
            session_id=request.get('session_id'),
            force_refresh=request.get('force_refresh', False)
        )
        
        response = await check_client_kb(kb_request)
        
        n8n_response = response.dict()
        
        if response.has_website_info:
            n8n_response['next_action'] = 'proceed_with_agent'
            n8n_response['routing'] = 'continue'
        else:
            n8n_response['next_action'] = 'request_website'
            n8n_response['routing'] = 'collect_info'
            n8n_response['prompt_message'] = "To provide you with accurate information about our services and pricing, could you please share your website URL?"
        
        return n8n_response
        
    except Exception as e:
        logger.error(f"Error in n8n_check_client_kb: {str(e)}")
        return {
            'status': 'error',
            'error': str(e),
            'next_action': 'handle_error',
            'routing': 'error'
        }

# Agent KB query endpoints
@app.post("/api/agent/query")
async def agent_kb_query(request: AgentKBQueryRequest):
    """Query agent with user message using dynamic KB context"""
    try:
        agent_context = await dynamic_agent_kb_handler.get_agent_context_from_kb(request.agent)
        
        client_context = await dynamic_agent_kb_handler.get_client_industry_context(request.user_id)
        
        kb_data = await client_kb_manager.get_client_kb(request.user_id, 'website_info')
        
        # Retrieve additional context from chat history
        chat_history = []
        try:
            history_result = supabase.table('chat_history')\
                .select('sender, message, timestamp')\
                .eq('user_id', request.user_id)\
                .order('timestamp', desc=True)\
                .limit(20)\
                .execute()
            if history_result.data:
                chat_history = history_result.data
        except Exception as e:
            logger.warning(f"Failed to retrieve chat history: {str(e)}")
        
        # Retrieve website data
        website_data = []
        try:
            website_result = supabase.table('website_data')\
                .select('url, analysis, created_at')\
                .eq('user_id', request.user_id)\
                .order('created_at', desc=True)\
                .limit(5)\
                .execute()
            if website_result.data:
                website_data = website_result.data
        except Exception as e:
            logger.warning(f"Failed to retrieve website data: {str(e)}")
        
        kb_context = {}
        if kb_data:
            content = kb_data.get('content', {})
            website_info = content.get('website_info', {})
            chat_insights = content.get('chat_insights', {})
            
            kb_context = {
                'company_name': website_info.get('company_name'),
                'website_url': website_info.get('url'),
                'services': website_info.get('services', []),
                'description': website_info.get('description'),
                'topics': chat_insights.get('topics', []),
                'interaction_count': content.get('interaction_history', {}).get('total_messages', 0)
            }
        
        # Enhance kb_context with chat history insights
        if chat_history:
            # Extract recent topics and context from chat history
            recent_messages = [msg['message'] for msg in chat_history[:10] if msg['sender'] == 'User']
            kb_context['recent_topics'] = recent_messages
            kb_context['chat_history_count'] = len(chat_history)
            
            # Find any mentions of websites, products, or services in recent chat
            for msg in chat_history[:10]:
                if msg['sender'] == 'User':
                    msg_lower = msg['message'].lower()
                    if any(url_indicator in msg_lower for url_indicator in ['http://', 'https://', 'www.', '.com', '.org']):
                        kb_context['mentioned_urls'] = kb_context.get('mentioned_urls', [])
                        kb_context['mentioned_urls'].append(msg['message'])
        
        # Enhance kb_context with website analysis data
        if website_data:
            kb_context['analyzed_websites'] = []
            for site in website_data:
                kb_context['analyzed_websites'].append({
                    'url': site['url'],
                    'analysis': site.get('analysis', ''),
                    'analyzed_at': site['created_at']
                })
        
        must_questions = agent_context.get('must_questions', [])
        missing_must_info = []
        
        for must_q in must_questions:
            if 'website' in must_q.lower() and not kb_context.get('website_url'):
                missing_must_info.append('website_url')
            elif 'niche' in must_q.lower() and client_context.get('niche') == 'Unknown':
                missing_must_info.append('client_niche')
            elif 'property address' in must_q.lower() and not kb_context.get('property_address'):
                missing_must_info.append('property_address')
        
        if 'website_url' in missing_must_info:
            follow_up_questions = await dynamic_agent_kb_handler.generate_contextual_questions(
                request.user_mssg,
                agent_context,
                ['website_url'],
                client_context
            )
            
            return AgentKBQueryResponse(
                user_id=request.user_id,
                agent=request.agent,
                response_type="needs_info",
                follow_up_questions=follow_up_questions,
                missing_information=[{
                    "field": "website_url",
                    "reason": "Required by agent's MUST questions to provide accurate analysis",
                    "priority": "critical"
                }],
                confidence_score=0.9,
                kb_context_used=False,
                status="missing_critical_info"
            )
        
        analysis = await dynamic_agent_kb_handler.analyze_query_with_context(
            request.user_mssg,
            agent_context,
            client_context,
            kb_context
        )
        
        if analysis.get('can_answer') and analysis.get('confidence', 0) > 0.7:
            available_tools = agent_context.get('tools', [])
            required_tool_names = analysis.get('required_tools', [])
            tools_to_use = [t for t in available_tools if t['name'] in required_tool_names]
            
            agent_response = await dynamic_agent_kb_handler.generate_contextual_response(
                request.user_mssg,
                agent_context,
                client_context,
                kb_context,
                tools_to_use
            )
            
            return AgentKBQueryResponse(
                user_id=request.user_id,
                agent=request.agent,
                response_type="needs_tools" if tools_to_use else "direct_answer",
                agent_response=agent_response,
                required_tools=tools_to_use if tools_to_use else None,
                confidence_score=analysis.get('confidence', 0.8),
                kb_context_used=bool(kb_context.get('company_name')),
                status="success"
            )
        
        else:
            all_missing = missing_must_info + analysis.get('missing_info', [])
            
            follow_up_questions = await dynamic_agent_kb_handler.generate_contextual_questions(
                request.user_mssg,
                agent_context,
                all_missing,
                client_context
            )
            
            missing_info_formatted = []
            for info in all_missing:
                missing_info_formatted.append({
                    "field": info,
                    "reason": f"Required by {request.agent} agent to provide accurate response",
                    "priority": "high" if info in missing_must_info else "medium"
                })
            
            return AgentKBQueryResponse(
                user_id=request.user_id,
                agent=request.agent,
                response_type="needs_info",
                follow_up_questions=follow_up_questions,
                missing_information=missing_info_formatted,
                confidence_score=analysis.get('confidence', 0.5),
                kb_context_used=bool(kb_context.get('company_name')),
                status="needs_more_info"
            )
            
    except Exception as e:
        logger.error(f"Error in agent_kb_query: {str(e)}")
        return AgentKBQueryResponse(
            user_id=request.user_id,
            agent=request.agent,
            response_type="error",
            agent_response="I encountered an error processing your request. Please try again.",
            confidence_score=0.0,
            kb_context_used=False,
            status="error"
        )

@app.post("/n8n/agent/query")
async def n8n_agent_kb_query(request: Dict[str, Any]):
    """N8N-compatible version of agent KB query endpoint"""
    try:
        query_request = AgentKBQueryRequest(
            user_id=request.get('user_id'),
            user_mssg=request.get('user_mssg'),
            agent=request.get('agent')
        )
        
        response = await agent_kb_query(query_request)
        
        n8n_response = response.dict()
        
        if response.response_type == "direct_answer":
            n8n_response['workflow_action'] = 'send_response'
            n8n_response['next_node'] = 'format_and_send'
            
        elif response.response_type == "needs_tools":
            n8n_response['workflow_action'] = 'execute_tools'
            n8n_response['next_node'] = 'tool_executor'
            n8n_response['tool_sequence'] = [t['name'] for t in (response.required_tools or [])]
            
        elif response.response_type == "needs_info":
            n8n_response['workflow_action'] = 'collect_information'
            n8n_response['next_node'] = 'info_collector'
            n8n_response['ui_action'] = 'show_form'
            
        n8n_response['execution_metadata'] = {
            'timestamp': datetime.now().isoformat(),
            'agent_type': request.get('agent'),
            'has_context': response.kb_context_used,
            'confidence': response.confidence_score
        }
        
        return n8n_response
        
    except Exception as e:
        logger.error(f"Error in n8n_agent_kb_query: {str(e)}")
        return {
            'status': 'error',
            'error': str(e),
            'workflow_action': 'handle_error',
            'next_node': 'error_handler'
        }

@app.post("/api/agent/refresh_kb")
async def refresh_agent_kb(agent_name: str):
    """Refresh agent KB by re-reading from agent_documents"""
    try:
        result = supabase.table('agent_documents')\
            .select('id')\
            .eq('agent_name', agent_name)\
            .limit(1)\
            .execute()
        
        if result.data:
            return {
                'status': 'success',
                'message': f'Agent KB for {agent_name} is available',
                'document_count': len(result.data)
            }
        else:
            return {
                'status': 'not_found',
                'message': f'No KB documents found for agent: {agent_name}'
            }
            
    except Exception as e:
        logger.error(f"Error refreshing agent KB: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Main n8n endpoints
@app.post("/n8n_main_req/{agent_name}/{session_id}")
async def n8n_main_request(request: N8nMainRequest, agent_name: str, session_id: str):
    """Handle main request to n8n workflow with conversational logic"""
    try:
        # Generate a unique request ID
        request_id = request.request_id or str(uuid.uuid4())
        
        request.agent_name = agent_name
        request.session_id = session_id
        
        # Skip empty messages
        if not request.user_mssg or request.user_mssg.strip() == "":
            logger.info("Skipping empty message")
            return {
                "status": "success",
                "message": "Empty message ignored",
                "request_id": request_id
            }
        
        # Deduplicate requests
        request_key = f"{session_id}:{request.user_mssg}:{agent_name}"
        current_time = time.time()
        
        # Check if duplicate within 2 seconds
        if request_key in request_cache:
            if current_time - request_cache[request_key] < 2.0:
                logger.info(f"Duplicate request detected: {request.user_mssg[:30]}...")
                return {
                    "status": "success",
                    "message": "Duplicate request ignored",
                    "request_id": request_id,
                    "agent_response": "Processing your previous message..."
                }
        
        request_cache[request_key] = current_time
        
        # Clean old cache entries
        for k in list(request_cache.keys()):
            if current_time - request_cache[k] > 10:
                del request_cache[k]
        
        # Only process the request if it's not an initial message or session change
        if request.user_mssg and request.user_mssg.strip() != "":
            request_data = {
                "user_id": request.user_id,
                "user_mssg": request.user_mssg,
                "session_id": request.session_id,
                "agent_name": request.agent_name,
                "timestamp_of_call_made": datetime.now().isoformat(),
                "request_id": request_id
            }
            
            # Check cache first
            cached_response = await conversational_handler.get_cached_response(request_id)
            if cached_response:
                logger.info(f"Returning cached response for request_id: {request_id}")
                return cached_response
                
            n8n_payload = await conversational_handler.handle_message(request_data)
            logger.info(f"Sending to n8n: {n8n_payload}")
            
            n8n_response = await call_n8n_webhook(n8n_payload)
            
            # Enhanced logging for debugging
            logger.debug(f"N8N Main Request - ID: {request_id}, Session: {session_id}, Agent: {agent_name}, Message: {request.user_mssg}, Response: {json.dumps(n8n_response, indent=2)}")
            
            logger.info(f"Received from n8n: {n8n_response}")
            
            formatted_response = {
                "user_id": n8n_response.get("user_id", request.user_id),
                "agent_name": n8n_response.get("agent_name", request.agent_name),
                "agent_response": n8n_response.get("agent_response", n8n_response.get("agent_responses", "")),
                "responses": n8n_response.get("responses", []),
                "timestamp": n8n_response.get("timestamp", datetime.now().isoformat()),
                "status": n8n_response.get("status", "success"),
                "request_id": request_id,
                "conversation_state": n8n_response.get("conversation_state", "complete"),
                "missing_info": n8n_response.get("missing_info", []),
                "images": extract_image_urls(n8n_response.get("agent_response", ""))
            }
            
            # Cache the response
            await conversational_handler.cache_response(request_id, formatted_response)
            
            await conversational_handler.save_to_history(
                request.session_id,
                request.user_id,
                request_data.get("_original_message", request.user_mssg),
                formatted_response["agent_response"]
            )
            
            return formatted_response
        else:
            # For initial messages or empty messages, just return a success response
            return {
                "status": "success",
                "message": "Initial message received",
                "request_id": request_id
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in n8n_main_request: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/n8n_main_req_stream")
async def n8n_main_request_stream(request: N8nMainRequest):
    """Handle streaming request to n8n workflow"""
    try:
        if not request.timestamp_of_call_made:
            request.timestamp_of_call_made = datetime.now().isoformat()
        
        request_data = {
            "user_id": request.user_id,
            "user_mssg": request.user_mssg,
            "session_id": request.session_id,
            "agent_name": request.agent_name,
            "timestamp_of_call_made": request.timestamp_of_call_made
        }
        
        n8n_payload = await conversational_handler.handle_message(request_data)
        
        return StreamingResponse(
            stream_n8n_response(n8n_payload),
            media_type="text/event-stream"
        )
        
    except Exception as e:
        logger.error(f"Error in n8n_main_request_stream: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Streaming endpoint
@app.post("/api/stream")
async def receive_stream_update(update: StreamUpdate):
    """Endpoint that receives streaming updates from n8n and forwards to WebSocket clients"""
    try:
        connection_id = f"{update.user_id}_{update.metadata.get('session_id', '')}"
        
        session_key = f"{update.user_id}:{update.metadata.get('request_id', '')}"
        if session_key not in streaming_sessions:
            streaming_sessions[session_key] = {
                "updates": [],
                "complete": False
            }
        
        streaming_sessions[session_key]["updates"].append(update.dict())
        
        if update.type in ["complete", "final"]:
            streaming_sessions[session_key]["complete"] = True
        
        if connection_id in active_connections:
            websocket = active_connections[connection_id]
            await websocket.send_json({
                "type": update.type,
                "agent": update.agent_name or update.agent_names,
                "message": update.message,
                "progress": update.progress,
                "requestId": update.metadata.get("request_id"),
                "metadata": update.metadata,
                "timestamp": int(time.time() * 1000)
            })
            
            if update.type == "complete" and update.agent_response:
                await websocket.send_json({
                    "type": "agent_response",
                    "agent": update.agent_name,
                    "message": update.agent_response,
                    "requestId": update.metadata.get("request_id"),
                    "final": True,
                    "timestamp": int(time.time() * 1000)
                })
        
        return {"status": "received", "connection_id": connection_id}
        
    except Exception as e:
        logger.error(f"Error in receive_stream_update: {str(e)}")
        return {"status": "error", "error": str(e)}
    
@app.post("/api/website/analyze")
async def analyze_website_endpoint(request: WebsiteAnalysisRequest):
    """
    Endpoint 1: Analyze website using Perplexity AI - NO TIMEOUTS
    """
    try:
        headers = {
            "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
            "Content-Type": "application/json"
        }
        
        prompt = f"""
        Please analyze the website {request.url} and provide a summary in exactly this format:
        --- *Company name*: [Extract company name]
        --- *Website*: {request.url}
        --- *Contact Information*: [Any available contact details]
        --- *Description*: [2-3 sentence summary of what the company does]
        --- *Tags*: [Main business categories, separated by periods]
        --- *Takeaways*: [Key business value propositions]
        --- *Niche*: [Specific market focus or specialty]
        """
        
        # NO TIMEOUT - let it take as long as needed
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers=headers,
                json={
                    "model": "sonar-reasoning-pro",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1000
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                analysis_text = result["choices"][0]["message"]["content"]
                
                # Parse the analysis
                parsed_analysis = {}
                lines = analysis_text.split('\n')
                for line in lines:
                    if '*Company name*:' in line:
                        parsed_analysis['company_name'] = line.split(':', 1)[1].strip()
                    elif '*Description*:' in line:
                        parsed_analysis['description'] = line.split(':', 1)[1].strip()
                    elif '*Niche*:' in line:
                        parsed_analysis['niche'] = line.split(':', 1)[1].strip()
                    elif '*Tags*:' in line:
                        parsed_analysis['tags'] = line.split(':', 1)[1].strip()
                    elif '*Takeaways*:' in line:
                        parsed_analysis['takeaways'] = line.split(':', 1)[1].strip()
                    elif '*Contact Information*:' in line:
                        parsed_analysis['contact_info'] = line.split(':', 1)[1].strip()
                
                # Save to database if user_id provided
                if request.user_id and request.session_id:
                    try:
                        supabase.table('website_data').upsert({
                            'user_id': request.user_id,
                            'session_id': request.session_id,
                            'url': request.url,
                            'analysis': json.dumps(parsed_analysis),
                            'created_at': datetime.now().isoformat()
                        }).execute()
                    except Exception as db_error:
                        logger.error(f"Error saving to database: {db_error}")
                
                return {
                    "status": "success",
                    "url": request.url,
                    "analysis": parsed_analysis,
                    "raw_analysis": analysis_text,
                    "timestamp": datetime.now().isoformat()
                }
            else:
                return {
                    "status": "error",
                    "message": f"Perplexity API error: {response.status_code}",
                    "details": response.text
                }
                
    except Exception as e:
        logger.error(f"Error in analyze_website_endpoint: {str(e)}")
        return {
            "status": "error",
            "message": str(e)
        }

@app.post("/api/website/screenshot")
async def capture_website_screenshot_endpoint(request: WebsiteScreenshotRequest):
    """
    Endpoint 2: Capture full website screenshot
    Returns screenshot URL from Supabase Storage
    """
    try:
        # Use the async version
        result = await capture_website_screenshot_async(
            url=request.url,
            session_id=request.session_id
        )
        
        # If successful and user_id provided, save metadata to database
        if result['status'] == 'success' and request.user_id:
            try:
                supabase.table('website_screenshots').upsert({
                    'user_id': request.user_id,
                    'session_id': request.session_id or str(uuid.uuid4()),
                    'url': request.url,
                    'screenshot_path': result['path'],
                    'public_url': result.get('public_url'),
                    'created_at': datetime.now().isoformat()
                }).execute()
            except Exception as db_error:
                logger.error(f"Error saving screenshot metadata: {db_error}")
        
        return {
            "status": result['status'],
            "message": result['message'],
            "screenshot_url": result.get('public_url'),
            "storage_path": result.get('path'),
            "filename": result.get('filename'),
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error in capture_website_screenshot_endpoint: {str(e)}")
        return {
            "status": "error",
            "message": str(e),
            "screenshot_url": None
        }

@app.post("/api/website/favicon")
async def get_website_favicon_endpoint(request: WebsiteFaviconRequest):
    """
    Endpoint 3: Extract and save website favicon/logo
    Returns favicon URL from Supabase Storage
    """
    try:
        # Use the async version
        result = await get_website_favicon_async(
            url=request.url,
            session_id=request.session_id
        )
        
        # If successful and user_id provided, save metadata to database
        if result['status'] == 'success' and request.user_id:
            try:
                supabase.table('website_favicons').upsert({
                    'user_id': request.user_id,
                    'session_id': request.session_id or str(uuid.uuid4()),
                    'url': request.url,
                    'favicon_path': result['path'],
                    'public_url': result.get('public_url'),
                    'created_at': datetime.now().isoformat()
                }).execute()
            except Exception as db_error:
                logger.error(f"Error saving favicon metadata: {db_error}")
        
        return {
            "status": result['status'],
            "message": result.get('message', 'Favicon processed'),
            "favicon_url": result.get('public_url'),
            "storage_path": result.get('path'),
            "filename": result.get('filename'),
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error in get_website_favicon_endpoint: {str(e)}")
        return {
            "status": "error",
            "message": str(e),
            "favicon_url": None
        }
    
@app.post("/api/website/full-analysis-async")
async def full_website_analysis_async(request: WebsiteAnalysisRequest):
    """
    Fire-and-forget endpoint that starts all operations and returns immediately
    """
    task_id = str(uuid.uuid4())
    
    # Initialize result structure
    background_results[task_id] = {
        "status": "started",
        "url": request.url,
        "session_id": request.session_id or str(uuid.uuid4()),
        "started_at": datetime.now().isoformat(),
        "analysis": {"status": "pending"},
        "screenshot": {"status": "pending"},
        "favicon": {"status": "pending"}
    }
    
    # Define async function to run all operations
    async def run_all_operations():
        try:
            # Run all three operations in parallel
            analysis_task = asyncio.create_task(analyze_website_endpoint(request))
            screenshot_task = asyncio.create_task(capture_website_screenshot_endpoint(
                WebsiteScreenshotRequest(
                    url=request.url,
                    session_id=request.session_id,
                    user_id=request.user_id
                )
            ))
            favicon_task = asyncio.create_task(get_website_favicon_endpoint(
                WebsiteFaviconRequest(
                    url=request.url,
                    session_id=request.session_id,
                    user_id=request.user_id
                )
            ))
            
            # Wait for each to complete and update results
            try:
                background_results[task_id]["analysis"] = await analysis_task
            except Exception as e:
                background_results[task_id]["analysis"] = {"status": "error", "message": str(e)}
            
            try:
                background_results[task_id]["screenshot"] = await screenshot_task
            except Exception as e:
                background_results[task_id]["screenshot"] = {"status": "error", "message": str(e)}
            
            try:
                background_results[task_id]["favicon"] = await favicon_task
            except Exception as e:
                background_results[task_id]["favicon"] = {"status": "error", "message": str(e)}
            
            # Update overall status
            all_success = all(
                background_results[task_id].get(key, {}).get("status") == "success" 
                for key in ["analysis", "screenshot", "favicon"]
            )
            background_results[task_id]["status"] = "complete"
            background_results[task_id]["overall_status"] = "success" if all_success else "partial_success"
            background_results[task_id]["completed_at"] = datetime.now().isoformat()
            
        except Exception as e:
            background_results[task_id]["status"] = "error"
            background_results[task_id]["error"] = str(e)
    
    # Start the operations without waiting
    asyncio.create_task(run_all_operations())
    
    # Return immediately
    return {
        "task_id": task_id,
        "status": "accepted",
        "message": "Analysis started in background",
        "check_url": f"/api/website/task/{task_id}",
        "url": request.url,
        "started_at": datetime.now().isoformat()
    }


# Add this endpoint to main.py if it doesn't exist
@app.get("/chat-history")
async def get_chat_history(session_id: str):
    """Get chat history for a session"""
    try:
        result = supabase.table('chat_history')\
            .select('*')\
            .eq('session_id', session_id)\
            .order('timestamp', desc=False)\
            .execute()
        
        if result.data:
            history = []
            for msg in result.data:
                history.append({
                    'sender': 'AI' if msg['sender'] == 'agent' else 'User',
                    'message': msg['message'],
                    'timestamp': msg['timestamp']
                })
            return {'history': history, 'status': 'success'}
        else:
            return {'history': [], 'status': 'success'}
            
    except Exception as e:
        logger.error(f"Error fetching chat history: {str(e)}")
        return {'history': [], 'status': 'error', 'error': str(e)}

# Application logs endpoint

# Keep last 100 log entries in memory
app_logs = deque(maxlen=100)

# Custom log handler to capture logs
class InMemoryLogHandler(logging.Handler):
    def emit(self, record):
        log_entry = {
            'timestamp': record.created,
            'level': record.levelname,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName
        }
        app_logs.append(log_entry)

# Add the handler to the logger
memory_handler = InMemoryLogHandler()
memory_handler.setLevel(logging.INFO)
logger.addHandler(memory_handler)

@app.get("/logs")
async def get_application_logs(limit: int = 50):
    """Get recent application logs"""
    try:
        # Get last N logs
        recent_logs = list(app_logs)[-limit:]
        
        # Format logs for response
        formatted_logs = []
        for log in recent_logs:
            formatted_logs.append({
                'timestamp': datetime.fromtimestamp(log['timestamp']).isoformat(),
                'level': log['level'],
                'message': log['message'],
                'module': log['module'],
                'function': log['function']
            })
        
        return {
            'status': 'success',
            'logs': formatted_logs,
            'count': len(formatted_logs),
            'total_available': len(app_logs)
        }
    except Exception as e:
        logger.error(f"Error fetching logs: {str(e)}")
        return {
            'status': 'error',
            'message': str(e),
            'logs': []
        }

# Combined endpoint that runs all three tools
@app.post("/api/website/full-analysis")
async def full_website_analysis(request: WebsiteAnalysisRequest):
    """
    Optimized for Heroku's 30-second limit
    Total execution time: max 25 seconds to leave buffer
    """
    try:
        results = {
            "url": request.url,
            "session_id": request.session_id or str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat()
        }
        
        # Start all tasks
        analysis_task = asyncio.create_task(analyze_website_endpoint(request))
        screenshot_task = asyncio.create_task(capture_website_screenshot_endpoint(
            WebsiteScreenshotRequest(
                url=request.url,
                session_id=request.session_id,
                user_id=request.user_id
            )
        ))
        favicon_task = asyncio.create_task(get_website_favicon_endpoint(
            WebsiteFaviconRequest(
                url=request.url,
                session_id=request.session_id,
                user_id=request.user_id
            )
        ))
        
        # Wait for all tasks with a total timeout of 25 seconds
        # This leaves 5 seconds buffer for response processing
        try:
            # Use gather with timeout for all tasks
            all_results = await asyncio.wait_for(
                asyncio.gather(
                    analysis_task,
                    screenshot_task,
                    favicon_task,
                    return_exceptions=True
                ),
                timeout=25.0  # Total timeout
            )
            
            # Process results
            results["analysis"] = all_results[0] if not isinstance(all_results[0], Exception) else {
                "status": "error",
                "message": str(all_results[0])
            }
            results["screenshot"] = all_results[1] if not isinstance(all_results[1], Exception) else {
                "status": "error", 
                "message": str(all_results[1])
            }
            results["favicon"] = all_results[2] if not isinstance(all_results[2], Exception) else {
                "status": "error",
                "message": str(all_results[2])
            }
            
        except asyncio.TimeoutError:
            # Timeout hit - get whatever is ready
            results["analysis"] = await analysis_task if analysis_task.done() else {
                "status": "timeout",
                "message": "Analysis timed out"
            }
            results["screenshot"] = await screenshot_task if screenshot_task.done() else {
                "status": "timeout",
                "message": "Screenshot timed out"
            }
            results["favicon"] = await favicon_task if favicon_task.done() else {
                "status": "timeout",
                "message": "Favicon timed out"
            }
        
        # Determine overall status
        all_success = all(
            results.get(key, {}).get("status") == "success" 
            for key in ["analysis", "screenshot", "favicon"]
        )
        
        results["overall_status"] = "success" if all_success else "partial_success"
        
        return results
        
    except Exception as e:
        logger.error(f"Error in full_website_analysis: {str(e)}")
        return {
            "status": "error",
            "message": str(e),
            "url": request.url
        }

# WebSocket endpoint
@app.websocket("/ws/{user_id}/{session_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str, session_id: str):
    """WebSocket endpoint that routes through n8n with streaming support"""
    connection_id = f"{user_id}_{session_id}"
    logger.info(f"New WebSocket connection: {connection_id}")
    
    await websocket.accept()
    
    active_connections[connection_id] = websocket
    last_activity = time.time()
    
    async def send_ping():
        """Send periodic ping to keep connection alive"""
        while connection_id in active_connections:
            try:
                await asyncio.sleep(30)  # Ping every 30 seconds
                if connection_id in active_connections:
                    await websocket.send_json({
                        "type": "ping",
                        "timestamp": int(time.time() * 1000)
                    })
            except Exception:
                break
    
    # Start ping task
    ping_task = asyncio.create_task(send_ping())
    
    try:
        # Send initial connection status
        await websocket.send_json({
            "type": "connection_status",
            "status": "connected",
            "message": "WebSocket connection established",
            "timestamp": int(time.time() * 1000)
        })
        
        while True:
            try:
                # Use timeout to prevent hanging
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
                message_data = json.loads(data)
                last_activity = time.time()
                
                request_id = message_data.get("requestId", str(uuid.uuid4()))
                
                user_input = message_data.get("message", "").strip()
                
                # Handle ping/pong and skip processing for empty messages  
                if message_data.get("type") == "ping":
                    await websocket.send_json({
                        "type": "pong",
                        "timestamp": int(time.time() * 1000)
                    })
                    continue
                elif message_data.get("type") == "pong":
                    continue
                elif not user_input or message_data.get("type") == "connection_status":
                    continue
                    
                # Check if this request is already being processed
                if request_id in active_requests:
                    logger.info(f"Request {request_id} is already being processed, skipping")
                    continue
                    
                active_requests.add(request_id)
                
                try:
                    await websocket.send_json({
                        "type": "ack",
                        "requestId": request_id,
                        "message": "Message received, processing...",
                        "timestamp": int(time.time() * 1000)
                    })
                    
                    n8n_payload = {
                        "user_id": user_id,
                        "user_mssg": user_input,
                        "session_id": session_id,
                        "agent_name": message_data.get("agent", "presaleskb"),
                        "timestamp_of_call_made": datetime.now().isoformat(),
                        "request_id": request_id
                    }
                    
                    asyncio.create_task(
                        process_n8n_request_async(n8n_payload, websocket, request_id)
                    )
                    
                finally:
                    active_requests.discard(request_id)
                    
            except asyncio.TimeoutError:
                logger.info(f"WebSocket timeout for {connection_id}, checking if still alive")
                try:
                    await websocket.send_json({
                        "type": "ping",
                        "timestamp": int(time.time() * 1000)
                    })
                except Exception:
                    logger.info(f"Connection {connection_id} appears dead, closing")
                    break
            except json.JSONDecodeError:
                logger.warning("Invalid JSON received, skipping")
                continue
                
    except WebSocketDisconnect:
        logger.info(f"Client disconnected: {connection_id}")
        
    except Exception as e:
        logger.exception(f"WebSocket error: {str(e)}")
        
    finally:
        # Clean up
        ping_task.cancel()
        if connection_id in active_connections:
            del active_connections[connection_id]
        logger.info(f"WebSocket connection closed: {connection_id}")


@app.get("/api/agents/config")
async def get_agents_config():
    """Get all agent configurations"""
    return {
        "agents": [agent.dict() for agent in AGENTS.values()]
    }

@app.get("/api/agents/config/{agent_name}")
async def get_agent_config_endpoint(agent_name: str):
    """Get specific agent configuration"""
    agent = get_agent_config(agent_name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_name} not found")
    return agent.dict()

def extract_image_urls(text: str) -> List[str]:
    """Extract Supabase storage URLs from text"""
    import re
    # Match Supabase storage URLs
    pattern = r'https://[^\s]+\.supabase\.co/storage/v1/[^\s]+\.(png|jpg|jpeg|gif|webp)'
    return re.findall(pattern, text, re.IGNORECASE)


@app.post("/api/website/analyze-background")
async def analyze_website_background(request: WebsiteAnalysisRequest, background_tasks: BackgroundTasks):
    """
    Start website analysis in background and return task ID immediately
    This avoids any timeout issues
    """
    task_id = str(uuid.uuid4())
    
    async def run_analysis():
        try:
            result = await analyze_website_endpoint(request)
            background_results[task_id] = {
                "status": "completed",
                "result": result,
                "completed_at": datetime.now().isoformat()
            }
        except Exception as e:
            background_results[task_id] = {
                "status": "failed",
                "error": str(e),
                "completed_at": datetime.now().isoformat()
            }
    
    # Start the background task
    background_tasks.add_task(run_analysis)
    
    background_results[task_id] = {
        "status": "processing",
        "started_at": datetime.now().isoformat(),
        "url": request.url
    }
    
    return {
        "task_id": task_id,
        "status": "processing",
        "message": "Analysis started in background",
        "check_url": f"/api/website/task/{task_id}"
    }

@app.get("/api/website/task/{task_id}")
async def get_background_task_result(task_id: str):
    """Check the status of a background task"""
    if task_id not in background_results:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return background_results[task_id]

@app.post("/api/send-invitation-email")
async def send_invitation_email(request: dict):
    """Send invitation email using backend Supabase client"""
    try:
        email = request.get('email')
        token = request.get('token')
        sender_name = request.get('senderName', 'Someone')
        invite_url = request.get('inviteUrl')
        
        if not email or not token or not invite_url:
            return {
                "success": False,
                "error": "Missing required fields",
                "details": f"Missing: {', '.join([k for k, v in {'email': email, 'token': token, 'invite_url': invite_url}.items() if not v])}"
            }
        
        print(f"Backend: Attempting to send invitation email to {email}")
        print(f"Backend: Invite URL: {invite_url}")
        
        # Try using the backend's Supabase client for admin operations
        try:
            # Check if user already exists
            existing_user = supabase.table('profiles').select('id, email').eq('email', email).execute()
            print(f"Backend: Existing user check: {len(existing_user.data) if existing_user.data else 0} users found")
            
            # For now, return success with manual link since SMTP might not be configured
            return {
                "success": False,
                "error": "Email sending not configured in backend",
                "fallback_url": invite_url,
                "message": f"Invitation created for {email}. Please share the link manually.",
                "invitation_details": {
                    "recipient": email,
                    "sender": sender_name,
                    "link": invite_url,
                    "token": token
                }
            }
            
        except Exception as supabase_error:
            print(f"Backend: Supabase operation failed: {str(supabase_error)}")
            return {
                "success": False,
                "error": "Backend database error",
                "details": str(supabase_error),
                "fallback_url": invite_url
            }
            
    except Exception as e:
        print(f"Backend: Send invitation email error: {str(e)}")
        return {
            "success": False,
            "error": "Backend invitation processing failed",
            "details": str(e)
        }

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)