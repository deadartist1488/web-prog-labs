from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Loan


class LoanRepository:
    def __init__(self, session: Session):
        self.s = session

    def get(self, loan_id: int) -> Optional[Loan]:
        return self.s.get(Loan, loan_id)

    def count_active(self, user_id: int) -> int:
        return self.s.scalar(
            select(func.count(Loan.id)).where(Loan.user_id == user_id, Loan.returned_at.is_(None))
        ) or 0

    def active_for(self, user_id: int, book_id: int) -> Optional[Loan]:
        return self.s.scalar(
            select(Loan).where(Loan.user_id == user_id, Loan.book_id == book_id, Loan.returned_at.is_(None))
        )

    def book_has_active(self, book_id: int) -> bool:
        return self.s.scalar(
            select(Loan.id).where(Loan.book_id == book_id, Loan.returned_at.is_(None)).limit(1)
        ) is not None

    def list_for_user(self, user_id: int, active_only: bool = False) -> List[Loan]:
        stmt = select(Loan).where(Loan.user_id == user_id)
        if active_only:
            stmt = stmt.where(Loan.returned_at.is_(None))
        return list(self.s.scalars(stmt.order_by(Loan.id)))

    def add(self, loan: Loan) -> Loan:
        self.s.add(loan)
        self.s.flush()
        return loan
