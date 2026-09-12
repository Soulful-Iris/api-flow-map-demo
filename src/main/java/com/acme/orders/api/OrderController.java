package com.acme.orders.api;

import com.acme.orders.service.OrderService;
import com.acme.orders.domain.*;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.security.access.prepost.PreAuthorize;
import javax.validation.Valid;
import java.util.List;

@RestController
@RequestMapping("/api/v1/orders")
public class OrderController {

    private final OrderService orderService;
    private final OrderMapper orderMapper;

    public OrderController(OrderService orderService, OrderMapper orderMapper) {
        this.orderService = orderService;
        this.orderMapper = orderMapper;
    }

    @GetMapping("/{id}")
    @PreAuthorize("hasRole('ORDER_READ')")
    public ResponseEntity<OrderDto> getOrder(@PathVariable("id") Long id, @RequestParam(required = false) boolean includeItems) {
        Order order = orderService.getOrder(id);
        if (order == null) {
            return ResponseEntity.notFound().build();
        }
        return ResponseEntity.ok(orderMapper.toDto(order, includeItems));
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    @PreAuthorize("hasRole('ORDER_WRITE')")
    public OrderDto createOrder(@Valid @RequestBody CreateOrderRequest request, @RequestHeader("X-Request-Id") String requestId) {
        Order order = orderService.placeOrder(request, requestId);
        return orderMapper.toDto(order, true);
    }

    @PostMapping("/{id}/cancel")
    public ResponseEntity<Void> cancelOrder(@PathVariable Long id) {
        orderService.cancel(id);
        return ResponseEntity.noContent().build();
    }

    @GetMapping
    public List<OrderDto> listOrders(@RequestParam("customerId") String customerId, @RequestParam(defaultValue = "20") int limit) {
        return orderService.findForCustomer(customerId, limit).stream().map(o -> orderMapper.toDto(o, false)).toList();
    }
}
