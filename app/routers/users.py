from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response, status
from typing_extensions import Annotated, Literal

from app.deps import get_current_user, get_loan_service, get_user_service, require_permission
from app.errors import BusinessRuleError
from app.models import User
from app.schemas import LoanOut, Page, UserCreate, UserOut, UserPatch, UserReplace
from app.services.books import LoanService
from app.services.users import UserService

router = APIRouter(prefix="/users", tags=["users"])

ERR = {400: {"description": "Ошибка валидации"}, 401: {"description": "Не авторизован"},
       403: {"description": "Нет прав"}, 404: {"description": "Не найден"}, 409: {"description": "Конфликт"}}


@router.get("", response_model=Page[UserOut], summary="Список пользователей (пагинация, поиск, сортировка)")
def list_users(
    svc: UserService = Depends(get_user_service),
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[Optional[str], Query(max_length=50)] = None,
    min_age: Annotated[Optional[int], Query(ge=18, le=120)] = None,
    sort: Literal["id", "name", "age", "created_at"] = "id",
    order: Literal["asc", "desc"] = "asc",
):
    return svc.list(page, size, search=search, min_age=min_age, sort=sort, order=order)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED, responses=ERR,
             summary="Создать пользователя")
def create_user(data: UserCreate, response: Response, svc: UserService = Depends(get_user_service)):
    user = svc.create(data)
    response.headers["Location"] = f"/users/{user.id}"
    return user


@router.get("/{user_id}", response_model=UserOut, responses=ERR, summary="Получить пользователя")
def get_user(user_id: int, svc: UserService = Depends(get_user_service)):
    return svc.get(user_id)


@router.put("/{user_id}", response_model=UserOut, responses=ERR, summary="Полностью обновить (сам или admin)")
def replace_user(user_id: int, data: UserReplace, svc: UserService = Depends(get_user_service),
                 actor: User = Depends(get_current_user)):
    return svc.replace(user_id, data, actor)


@router.patch("/{user_id}", response_model=UserOut, responses=ERR, summary="Частично обновить (сам или admin)")
def patch_user(user_id: int, data: UserPatch, svc: UserService = Depends(get_user_service),
               actor: User = Depends(get_current_user)):
    return svc.patch(user_id, data, actor)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, responses=ERR,
               summary="Удалить (сам или admin)")
def delete_user(user_id: int, svc: UserService = Depends(get_user_service),
                actor: User = Depends(get_current_user)):
    svc.delete(user_id, actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{user_id}/loans", response_model=List[LoanOut], responses=ERR,
            summary="Выдачи пользователя (сам или admin)")
def user_loans(user_id: int, active: bool = False, svc: LoanService = Depends(get_loan_service),
               actor: User = Depends(get_current_user)):
    return svc.for_user(user_id, actor, active_only=active)


@router.put("/{user_id}/role", response_model=UserOut, responses=ERR, summary="Сменить роль (только admin)")
def set_role(user_id: int, role: Literal["user", "librarian", "admin"],
             svc: UserService = Depends(get_user_service), admin: User = Depends(require_permission("users.admin"))):
    if user_id == admin.id and role != "admin":
        raise BusinessRuleError("Нельзя снять роль admin с самого себя")
    return svc.set_role(user_id, role)
