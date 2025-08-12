import os
import asyncio
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
import httpx

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# دیتابیس SQLite
engine = create_engine("sqlite:///pestalert.db", connect_args={"check_same_thread": False})
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

class Farm(Base):
    __tablename__ = "farms"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    alerts = relationship("Alert", back_populates="farm")

class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True, index=True)
    farm_id = Column(Integer, ForeignKey("farms.id"))
    message = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    farm = relationship("Farm", back_populates="alerts")

Base.metadata.create_all(bind=engine)

class FarmCreate(BaseModel):
    name: str
    latitude: float
    longitude: float

class FarmOut(BaseModel):
    id: int
    name: str
    latitude: float
    longitude: float

    class Config:
        orm_mode = True

class AlertOut(BaseModel):
    id: int
    farm_id: int
    message: str
    timestamp: datetime

    class Config:
        orm_mode = True

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

OWM_API_KEY = "191dcdac9846b2d243b87cc12c9fe376"  # go to open weather map and get an API

async def get_weather(lat: float, lon: float) -> dict:
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&units=metric&appid={OWM_API_KEY}"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    main = data.get("main", {})
    rain = 0
    if "rain" in data:
        rain = data["rain"].get("1h", 0) if isinstance(data["rain"], dict) else 0
    return {
        "temperature": main.get("temp"),
        "humidity": main.get("humidity"),
        "rain_mm": rain,
        "raw": data
    }

def evaluate_rules(weather: dict) -> List[str]:
    pests = []
    temp = weather.get("temperature")
    hum = weather.get("humidity")
    rain = weather.get("rain_mm", 0) or 0

    if hum is not None and temp is not None:
        if hum > 80 and 20 <= temp <= 28:
            pests.append("Powdery mildew (possible)")
        if temp > 30 and hum < 40:
            pests.append("Aphids (possible)")
        if hum > 85 and rain > 5:
            pests.append("Gray mold / Botrytis (possible)")
    return pests

from fastapi import Depends

@app.post("/farms/", response_model=FarmOut)
def create_farm(farm_in: FarmCreate, db=Depends(get_db)):
    farm = Farm(name=farm_in.name, latitude=farm_in.latitude, longitude=farm_in.longitude)
    db.add(farm)
    db.commit()
    db.refresh(farm)
    return farm

@app.get("/farms/", response_model=List[FarmOut])
def list_farms(db=Depends(get_db)):
    return db.query(Farm).all()

@app.get("/alerts/", response_model=List[AlertOut])
def list_alerts(db=Depends(get_db)):
    return db.query(Alert).order_by(Alert.timestamp.desc()).limit(100).all()

INTERNAL_TOKEN = "my-secret-token"  #you can change it as you like!!

@app.post("/internal/run-checks")
async def run_checks(token: Optional[str] = None, db=Depends(get_db)):
    if token != INTERNAL_TOKEN:
        raise HTTPException(status_code=403, detail="forbidden")
    farms = db.query(Farm).all()
    for farm in farms:
        weather = await get_weather(farm.latitude, farm.longitude)
        pests = evaluate_rules(weather)
        if pests:
            msg = f"Farm '{farm.name}' potential pests: " + "; ".join(pests)
            alert = Alert(farm_id=farm.id, message=msg)
            db.add(alert)
            db.commit()
    return {"status": "done", "checked": len(farms)}
