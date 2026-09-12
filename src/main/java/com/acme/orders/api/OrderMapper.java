package com.acme.orders.api;

import com.acme.orders.domain.*;
import org.springframework.stereotype.Component;

@Component
public class OrderMapper {
    public OrderDto toDto(Order order, boolean includeItems) {
        OrderDto dto = new OrderDto();
        dto.setId(order.getId());
        if (includeItems) {
            dto.setItems(order.getItems());
        }
        return dto;
    }
}
