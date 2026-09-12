package com.acme.orders.domain;

public class PaymentDeclinedException extends RuntimeException {
    public PaymentDeclinedException(String reason) { super(reason); }
}
