package com.acme.orders.domain;
import java.util.List; import java.math.BigDecimal;
public class Types {}
enum OrderStatus { NEW, CONFIRMED, UNDER_REVIEW, CANCELLED }
class OrderItem { String sku; int qty; }
class OrderDto { void setId(Long id) {} void setItems(List<OrderItem> i) {} }
class CreateOrderRequest { List<OrderItem> getItems() { return null; } String getPaymentToken() { return ""; } BigDecimal total() { return null; } }
class ChargeRequest { ChargeRequest(BigDecimal a, String t, String r) {} }
class PaymentResult { String getPaymentId() { return ""; } }
class InventoryStatus { boolean isAvailable() { return true; } List<String> getMissingSkus() { return null; } }
class FraudResponse { int getScore() { return 0; } }
class OrderFlaggedEvent { OrderFlaggedEvent(Long id, int s) {} }
class OrderPlacedEvent { OrderPlacedEvent(Long id) {} }
class OrderCancelledEvent { OrderCancelledEvent(Long id) {} }
class ErrorBody { ErrorBody(String m) {} }
