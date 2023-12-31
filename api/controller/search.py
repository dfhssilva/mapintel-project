import json
import logging
import time
from typing import Dict, List, Optional, Union

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.controller.utils import RequestLimiter
from api.utils import load_document_store, load_pipeline_from_yaml

logger = logging.getLogger(__name__)
router = APIRouter()
query_pipeline = load_pipeline_from_yaml("query")
document_store = load_document_store()
concurrency_limiter = RequestLimiter(4)


class Request(BaseModel):
    query: str
    filters: Optional[List[dict]] = None
    top_k_retriever: Optional[int]
    top_k_reader: Optional[int]


class Answer(BaseModel):
    answer: Optional[str]
    score: Optional[float] = None
    document_id: Optional[str] = None
    meta: Optional[Dict[str, Optional[Union[str, List]]]]


class Response(BaseModel):
    query: str
    answers: List[Answer]


class Request_generator(BaseModel):
    filters: Optional[List[dict]] = None
    batch_size: Optional[int] = None


class Request_count(BaseModel):
    filters: Optional[List[dict]] = None


class Response_count(BaseModel):
    num_documents: int


@router.post("/query", response_model=Response)
def query(request: Request):
    """Query endpoint.

    Performs a query on the document store based on semantic search and approximate
    nearest neighbors. Also, applies boolean filters to the documents before performing
    semantic search.
    """
    with concurrency_limiter.run():
        result = _process_request(query_pipeline, request)
        return result


@router.post("/all-docs-generator")
def all_docs_generator(request: Request_generator):
    """All Docs Generator endpoint.

    Returns a Streaming Response consisting of a generator that iterates over the document store,
    given a set of boolean filters. The documents aren't iterated in any particular order
    as can be confirmed in: https://elasticsearch-py.readthedocs.io/en/v7.11.0/helpers.html#elasticsearch.helpers.scan.
    This generator can be used to obtain random samples of the document store without
    having to hold all documents in memory.
    """
    result_generator = document_store.get_all_documents_generator(
        filters=request.filters, return_embedding=False, batch_size=request.batch_size
    )
    # Define answers generator (adds procedures to the previous generator)
    answers = _encoded_results(result_generator)
    return StreamingResponse(answers)


@router.post("/doc-count", response_model=Response_count)
def doc_count(request: Request_count):
    """Doc Count endpoint.

    Gets the number of documents in the document store that satisfy a particular
    boolean filter.
    """
    num_docs = document_store.get_document_count(filters=request.filters)
    return {"num_documents": num_docs}


def _encoded_results(results):
    """Reference: https://github.com/encode/starlette/issues/419#issuecomment-470077657."""
    for idx, doc in enumerate(results):
        if idx > 0:
            yield "#SEP#"  # delimiter
        yield json.dumps({"answer": doc.text, "document_id": doc.id, "meta": doc.meta})


def _process_request(pipeline, request) -> Response:
    start_time = time.time()

    result = pipeline.run(
        query=request.query,
        filters=request.filters,
        top_k_retriever=request.top_k_retriever,
        top_k_reader=request.top_k_reader,
    )

    end_time = time.time()
    logger.info(
        json.dumps(
            {
                "request": request.dict(),
                "response": str(result),
                "time": f"{(end_time - start_time):.2f}",
            }
        )
    )

    return result
