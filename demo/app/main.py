from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import secrets
from threading import BoundedSemaphore,Lock
from collections import deque
import time
from typing import Literal
from fastapi import Depends,FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,JSONResponse
from fastapi.security import HTTPBasic,HTTPBasicCredentials
from pydantic import BaseModel,ConfigDict,Field,field_validator
from .retrieval import Engine

STATIC=Path(__file__).parent/'static'

class MatchRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    note:str=Field(min_length=20,max_length=12000)
    method:Literal['bm25','fusion','rerank']|None=None
    top_k:int=Field(default=10,ge=1,le=10)

    @field_validator('note')
    @classmethod
    def meaningful_note(cls,value):
        value=value.strip()
        if len(value)<20 or not any(c.isalpha() for c in value):raise ValueError('Enter a research patient description of at least 20 characters')
        return value

def create_app(engine=None):
    if engine is None:
        config=json.loads((Path(__file__).parent/'config.json').read_text(encoding='utf-8'))
        engine=Engine(os.environ.get('TRIAL_INDEX_PATH','data/indexes/bm25.sqlite'),os.environ.get('RERANK_MODEL_DIR') or None,expected_records=None,config=config)
    selected_method=engine.config.get('default_method','fusion')
    if selected_method not in {'bm25','fusion','rerank'}:raise ValueError('Invalid configured pipeline')
    username=os.environ.get('DEMO_USERNAME','');password=os.environ.get('DEMO_PASSWORD','')
    require_auth=os.environ.get('REQUIRE_AUTH','false').lower()=='true'
    if require_auth and (not username or not password):raise RuntimeError('REQUIRE_AUTH needs DEMO_USERNAME and DEMO_PASSWORD')
    if bool(username)!=bool(password):raise RuntimeError('Set both demo authentication variables or neither')
    security=HTTPBasic(auto_error=False);gate=BoundedSemaphore(1)
    recent=deque();rate_lock=Lock();rate_limit=int(os.environ.get('MAX_SEARCHES_PER_MINUTE','10'))
    if not 1<=rate_limit<=120:raise ValueError('MAX_SEARCHES_PER_MINUTE must be between 1 and 120')
    def authenticate(credentials:HTTPBasicCredentials|None=Depends(security)):
        if not username:return
        if credentials is None or not (secrets.compare_digest(credentials.username.encode(),username.encode()) and secrets.compare_digest(credentials.password.encode(),password.encode())):
            raise HTTPException(401,'Authentication required',headers={'WWW-Authenticate':'Basic'})

    @asynccontextmanager
    async def lifespan(app):
        engine.load();yield
        engine.ready=False
    app=FastAPI(title='Clinical Trial Matcher',version='0.3.0',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.engine=engine

    @app.middleware('http')
    async def headers(request:Request,call_next):
        length=request.headers.get('content-length','0')
        if length.isdigit() and int(length)>65536:return JSONResponse({'detail':'Request is too large'},status_code=413)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        return response

    @app.get('/health/live')
    def live():return {'status':'alive'}

    @app.get('/health/ready')
    def ready():
        if not engine.ready:raise HTTPException(503,'Index is not ready')
        return {'status':'ready'}

    @app.get('/',dependencies=[Depends(authenticate)],include_in_schema=False)
    def home():return FileResponse(STATIC/'index.html')

    @app.get('/assets/{name}',dependencies=[Depends(authenticate)],include_in_schema=False)
    def asset(name:str):
        if name not in {'app.js','style.css'}:raise HTTPException(404)
        return FileResponse(STATIC/name)

    @app.get('/config',dependencies=[Depends(authenticate)])
    def config():
        return dict(**engine.dataset_info(),reranking_available=engine.encoder is not None,default_method=selected_method)

    @app.get('/openapi.json',dependencies=[Depends(authenticate)],include_in_schema=False)
    def openapi():return app.openapi()

    @app.post('/match',dependencies=[Depends(authenticate)])
    def match(body:MatchRequest):
        if not engine.ready:raise HTTPException(503,'Index is not ready')
        if not gate.acquire(blocking=False):raise HTTPException(429,'A search is already running. Please retry shortly.',headers={'Retry-After':'5'})
        try:
            with rate_lock:
                now=time.monotonic()
                while recent and recent[0]<=now-60:recent.popleft()
                if len(recent)>=rate_limit:raise HTTPException(429,'Demo search limit reached. Please retry in a minute.',headers={'Retry-After':str(max(1,int(61-(now-recent[0]))))})
                recent.append(now)
            return engine.search(body.note,body.method or selected_method,body.top_k)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        finally:gate.release()
    return app

app=create_app()
