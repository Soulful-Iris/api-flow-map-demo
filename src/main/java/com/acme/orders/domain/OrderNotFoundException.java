package com.acme.orders.domain;

public class OrderNotFoundException extends RuntimeException {
    public OrderNotFoundException(Long id) { super("order " + id + " not found"); }
}
