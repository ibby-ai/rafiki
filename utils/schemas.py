from pydantic import BaseModel

class QueryBody(BaseModel):
    question: str