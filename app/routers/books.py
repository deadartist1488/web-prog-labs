from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from typing_extensions import Annotated, Literal

from app.deps import (
    get_book_service,
    get_current_user,
    get_loan_service,
    get_review_service,
    require_permission,
)
from app.errors import BusinessRuleError
from app.models import User
from app.schemas import BookCreate, BookOut, BookPatch, LoanOut, Page, ReviewIn, ReviewOut
from app.services.books import BookService, LoanService, ReviewService

router = APIRouter(tags=["books"])

ERR = {400: {"description": "Ошибка валидации"}, 401: {"description": "Не авторизован"},
       403: {"description": "Нет прав"}, 404: {"description": "Не найдено"}, 409: {"description": "Конфликт"},
       422: {"description": "Нарушено бизнес-правило"}}


@router.get("/books", response_model=Page[BookOut], summary="Каталог: фильтры, поиск, сортировка, пагинация")
def list_books(
    svc: BookService = Depends(get_book_service),
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[Optional[str], Query(max_length=100, description="поиск по названию и автору")] = None,
    author: Optional[str] = None,
    genre: Optional[str] = None,
    min_price: Annotated[Optional[Decimal], Query(ge=0)] = None,
    max_price: Annotated[Optional[Decimal], Query(ge=0)] = None,
    available: Optional[bool] = None,
    sort: Literal["id", "title", "year", "price"] = "id",
    order: Literal["asc", "desc"] = "asc",
):
    if min_price is not None and max_price is not None and min_price > max_price:
        raise BusinessRuleError("min_price не может быть больше max_price",
                                [{"field": "min_price", "message": "больше max_price"}])
    return svc.list(page, size, q=q, author=author, genre=genre, min_price=min_price, max_price=max_price,
                    available=available, sort=sort, order=order)


@router.post("/books", response_model=BookOut, status_code=201, responses=ERR,
             summary="Добавить книгу (librarian/admin)")
def create_book(data: BookCreate, response: Response, svc: BookService = Depends(get_book_service),
                _: User = Depends(require_permission("books.write"))):
    book = svc.create(data)
    response.headers["Location"] = f"/books/{book.id}"
    return book


@router.get("/books/{book_id}", response_model=BookOut, responses=ERR, summary="Книга (кэшируется в Redis)")
def get_book(book_id: int, svc: BookService = Depends(get_book_service)):
    return svc.get_cached(book_id)


@router.patch("/books/{book_id}", response_model=BookOut, responses=ERR, summary="Изменить книгу (librarian/admin)")
def patch_book(book_id: int, data: BookPatch, svc: BookService = Depends(get_book_service),
               _: User = Depends(require_permission("books.write"))):
    return svc.patch(book_id, data)


@router.delete("/books/{book_id}", status_code=204, responses=ERR, summary="Удалить книгу (librarian/admin)")
def delete_book(book_id: int, svc: BookService = Depends(get_book_service),
                _: User = Depends(require_permission("books.write"))):
    svc.delete(book_id)
    return Response(status_code=204)


@router.post("/books/{book_id}/loans", response_model=LoanOut, status_code=201, responses=ERR,
             summary="Взять книгу (поддерживает Idempotency-Key)")
def borrow(book_id: int, request: Request, svc: LoanService = Depends(get_loan_service),
           user: User = Depends(get_current_user),
           idempotency_key: Annotated[Optional[str], Header(max_length=100)] = None):
    store = request.app.state.idempotency
    scope = f"borrow:{user.id}:{book_id}"
    if idempotency_key:
        saved = store.get(scope, idempotency_key)
        if saved:  # повтор того же запроса (например, клиент не дождался ответа) — без второй выдачи
            return JSONResponse(saved["body"], status_code=saved["status"], headers={"Idempotent-Replay": "true"})
    loan = svc.borrow(user, book_id)
    body = jsonable_encoder(LoanOut.model_validate(loan))
    if idempotency_key:
        store.save(scope, idempotency_key, 201, body)
    return JSONResponse(body, status_code=201)


@router.post("/loans/{loan_id}/return", response_model=LoanOut, responses=ERR, summary="Вернуть книгу")
def give_back(loan_id: int, svc: LoanService = Depends(get_loan_service), user: User = Depends(get_current_user)):
    return svc.give_back(loan_id, user)


@router.get("/books/{book_id}/reviews", response_model=List[ReviewOut], responses=ERR, summary="Отзывы о книге")
def list_reviews(book_id: int, svc: ReviewService = Depends(get_review_service)):
    return svc.list_for_book(book_id)


@router.post("/books/{book_id}/reviews", response_model=ReviewOut, status_code=status.HTTP_201_CREATED,
             responses=ERR, summary="Оставить отзыв")
def create_review(book_id: int, data: ReviewIn, svc: ReviewService = Depends(get_review_service),
                  user: User = Depends(require_permission("reviews.write"))):
    return svc.create(book_id, data, user)


@router.put("/reviews/{review_id}", response_model=ReviewOut, responses=ERR, summary="Изменить отзыв (автор/admin)")
def update_review(review_id: int, data: ReviewIn, svc: ReviewService = Depends(get_review_service),
                  user: User = Depends(get_current_user)):
    return svc.update(review_id, data, user)


@router.delete("/reviews/{review_id}", status_code=204, responses=ERR, summary="Удалить отзыв (автор/admin)")
def delete_review(review_id: int, svc: ReviewService = Depends(get_review_service),
                  user: User = Depends(get_current_user)):
    svc.delete(review_id, user)
    return Response(status_code=204)
