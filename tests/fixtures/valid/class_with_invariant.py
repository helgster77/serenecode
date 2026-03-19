"""A module with a class that has proper invariants and contracted methods."""

import icontract


@icontract.invariant(lambda self: self._balance >= 0, "balance must be non-negative")
class Wallet:
    """A simple wallet with non-negative balance."""

    @icontract.require(lambda initial: initial >= 0, "initial must be non-negative")
    @icontract.ensure(lambda self: self._balance >= 0, "balance must be non-negative after init")
    def __init__(self, initial: float) -> None:
        """Initialize wallet with a non-negative balance."""
        self._balance = initial

    @icontract.require(lambda amount: amount > 0, "amount must be positive")
    @icontract.ensure(lambda self, OLD: self._balance == OLD.self._balance + amount, "balance must increase")
    def deposit(self, amount: float) -> None:
        """Deposit a positive amount."""
        self._balance += amount

    @icontract.require(lambda self, amount: 0 < amount <= self._balance, "amount valid and sufficient")
    @icontract.ensure(lambda self, OLD: self._balance == OLD.self._balance - amount, "balance must decrease")
    def withdraw(self, amount: float) -> None:
        """Withdraw a valid amount."""
        self._balance -= amount

    @icontract.require(lambda self: True, "self must exist")
    @icontract.ensure(lambda result: result >= 0, "balance must be non-negative")
    def get_balance(self) -> float:
        """Get the current balance."""
        return self._balance
