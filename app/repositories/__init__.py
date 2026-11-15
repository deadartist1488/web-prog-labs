"""Repository-слой (лаба 4): только доступ к данным, никаких бизнес-правил и HTTP."""
from app.repositories.books import BookRepository
from app.repositories.loans import LoanRepository
from app.repositories.reviews import ReviewRepository
from app.repositories.users import UserRepository

__all__ = ["UserRepository", "BookRepository", "LoanRepository", "ReviewRepository"]
