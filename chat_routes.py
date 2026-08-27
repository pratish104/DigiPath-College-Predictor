from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from database import get_db
import models
from chat_service import ChatService
from data_loader import DataLoader
import os
from pydantic import BaseModel

router = APIRouter(prefix="/chat", tags=["AI Assistant"])

# Initialize Service
base_path = os.path.dirname(os.path.abspath(__file__))
data_dir = os.path.join(base_path, "data")
inst_json = os.path.join(base_path, "institutes.json")
loader = DataLoader(data_dir, inst_json)
chat_service = ChatService(loader)

class ChatQuery(BaseModel):
    query: str

# Helper to get current user from cookies
async def get_current_user_id(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token or not token.startswith("Bearer "):
        return None
    
    from auth_service import decode_access_token
    payload = decode_access_token(token.split(" ")[1])
    if not payload:
        return None
        
    email = payload.get("sub")
    import models
    user = db.query(models.User).filter(models.User.email == email).first()
    return user.id if user else None

@router.post("/query")
async def chat_query(req: ChatQuery, request: Request, db: Session = Depends(get_db)):
    user_id = await get_current_user_id(request, db)
    
    # Get history for context if user logged in
    history = []
    if user_id:
        hists = db.query(models.ChatHistory).filter(models.ChatHistory.user_id == user_id).order_by(models.ChatHistory.created_at.desc()).limit(5).all()
        history = [{"query": h.query, "response": h.response} for h in reversed(hists)]

    response = chat_service.get_contextual_response(req.query, history)
    
    # Store in DB
    if user_id:
        db_history = models.ChatHistory(
            user_id=user_id,
            query=req.query,
            response=response
        )
        db.add(db_history)
        db.commit()

    return {"response": response}

@router.get("/history")
async def get_chat_history(request: Request, db: Session = Depends(get_db)):
    user_id = await get_current_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    return db.query(models.ChatHistory).filter(models.ChatHistory.user_id == user_id).order_by(models.ChatHistory.created_at.desc()).all()
